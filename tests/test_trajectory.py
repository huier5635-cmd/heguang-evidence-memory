import json
import zlib

from fastapi.testclient import TestClient

from heguang_code.api import create_app
from heguang_code.config import Settings


def test_empty_assistant_commands_tool_outputs_and_final_patch_are_preserved(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, api_key="test", profile="rarity"))
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1", "function": {
            "name": "bash", "arguments": json.dumps({"command": "pytest tests/test_packet.py -q"})}}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "Wrapped response",
         "extra": {"raw_output": "FAILED tests/test_packet.py::test_missing_packet\nExact failure details", "returncode": 1}},
        {"role": "exit", "content": "", "extra": {"submission": "diff --git a/src/packet.py b/src/packet.py\n+def parse_packet(value):\n+    return value or []\n"}},
    ]
    payload = {"request_id": "r", "user_id": "u", "session_id": "history", "messages": messages}
    with TestClient(app, headers={"Authorization": "Bearer test"}) as client:
        assert client.post("/add", json=payload).status_code == 200
        result = client.post("/search", json={"user_id": "u", "query": "parse_packet tests/test_packet.py", "top_k": 100}).json()
    evidence = "\n".join(row["content"] for row in result["data"])
    assert "pytest tests/test_packet.py -q" in evidence
    assert "Exact failure details" in evidence and "return value or []" in evidence
    assert any("pytest tests/test_packet.py -q" in row["content"] and "Exact failure details" in row["content"]
               and '"paired_source_id"' in row["content"] for row in result["data"])
    db = app.state.store.connect("u", create=False)
    originals = [json.loads(zlib.decompress(row[0])) for row in db.execute("SELECT body FROM raw_messages")]
    assert sorted(originals, key=lambda value: value["role"]) == sorted(messages, key=lambda value: value["role"])
    db.close()


def test_tool_cannot_pair_with_a_future_or_unrelated_command():
    from heguang_code.normalize import trajectory_views
    from heguang_code.schema import Message
    messages = [
        Message(role="tool", content="unpaired output", tool_call_id="later"),
        Message(role="assistant", content="", tool_calls=[{"id": "later", "function": {"arguments": '{"command":"echo later"}'}}]),
        Message(role="tool", content="different output", tool_call_id="different"),
    ]
    views = list(trajectory_views(messages))
    assert views[0][2] is None and views[2][2] is None
    assert "echo later" not in views[0][0] and "echo later" not in views[2][0]


def test_conflicting_or_reused_tool_identity_keeps_output_unpaired():
    from heguang_code.normalize import trajectory_views
    from heguang_code.schema import Message
    calls = [{"id": "collision", "function": {"arguments": '{"command":"actual_command"}'}}]
    messages = [
        Message(role="assistant", tool_calls=calls,
                extra={"actions": [{"tool_call_id": "collision", "command": "contradictory_command"}]}),
        Message(role="tool", content="source_output", tool_call_id="collision"),
        Message(role="assistant", tool_calls=calls),
        Message(role="tool", content="another_output", tool_call_id="collision"),
    ]
    views = list(trajectory_views(messages))
    assert views[1][2] is None and views[3][2] is None
    assert views[1][0] == "source_output" and views[3][0] == "another_output"


def test_paired_trajectory_expires_atomically(tmp_path):
    from heguang_code.schema import AddRequest
    from heguang_code.store import Store
    store = Store(Settings(data_dir=tmp_path, api_key="test"))
    store.add(AddRequest(user_id="u", session_id="s", request_id="old", messages=[
        {"role": "assistant", "tool_calls": [{"id": "c", "function": {"arguments": '{"command":"old_command"}'}}]},
        {"role": "tool", "content": "old output", "tool_call_id": "c"},
    ]))
    db = store.connect("u")
    db.execute("UPDATE sources SET added_at=0")
    db.execute("UPDATE writes SET added_at=0")
    db.commit()
    db.close()
    store.purge_expired()
    db = store.connect("u", create=False)
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    for table in ("sources", "raw_messages", "call_state", "blocks", "blobs", "docs", "spans", "writes"):
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    db.close()


def test_command_pairs_across_add_and_restart_only_inside_same_user_and_session(tmp_path):
    from heguang_code.schema import AddRequest, SearchRequest
    from heguang_code.store import Store
    settings = Settings(data_dir=tmp_path, api_key="test", profile="rarity")
    command = {"role": "assistant", "tool_calls": [{"id": "call-split", "function": {
        "arguments": '{"command":"pytest tests/test_split.py -q"}'}}]}
    output = {"role": "tool", "content": "split_marker_7824 exact output", "tool_call_id": "call-split"}
    Store(settings).add(AddRequest(user_id="u", request_id="command", session_id="same", messages=[command]))
    store = Store(settings)
    for user, session, rid in [("u", "same", "output"), ("u", "unrelated", "other-session"), ("other", "same", "other-user")]:
        store.add(AddRequest(user_id=user, request_id=rid, session_id=session, messages=[output]))
        db = store.connect(user, create=False)
        source = db.execute("SELECT * FROM sources WHERE request_id=?", (rid,)).fetchone()
        text = store.source_span(db, source["id"], 0, source["chars"], {})
        assert bool(source["paired_source_id"]) == (user == "u" and session == "same")
        assert ("pytest tests/test_split.py -q" in text) == (user == "u" and session == "same")
        db.close()
    rows = store.search(SearchRequest(user_id="u", query="split_marker_7824", top_k=100))
    assert any("pytest tests/test_split.py -q" in row["content"] and '"paired_source_id"' in row["content"] for row in rows)


def test_parent_expiry_detaches_fresh_output_and_removes_command_state(tmp_path):
    from heguang_code.schema import AddRequest, SearchRequest
    from heguang_code.store import Store
    store = Store(Settings(data_dir=tmp_path, api_key="test"))
    store.add(AddRequest(user_id="u", request_id="old", session_id="s", messages=[
        {"role": "assistant", "tool_calls": [{"id": "c", "function": {"arguments": '{"command":"parent_command"}'}}]}]))
    store.add(AddRequest(user_id="u", request_id="fresh", session_id="s", messages=[
        {"role": "tool", "content": "fresh_marker output", "tool_call_id": "c"}]))
    db = store.connect("u")
    db.execute("UPDATE sources SET added_at=0 WHERE request_id='old'")
    db.execute("UPDATE writes SET added_at=0 WHERE request_id='old'")
    db.commit()
    db.close()
    store.purge_expired()
    rows = store.search(SearchRequest(user_id="u", query="fresh_marker", top_k=100))
    assert rows and '"paired_parent_expired":true' in rows[0]["content"]
    db = store.connect("u", create=False)
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert db.execute("SELECT count(*) FROM call_state").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM sources").fetchone()[0] == 1
    db.close()


def test_oversized_command_is_retained_without_repeating_it_into_outputs():
    from heguang_code.normalize import PAIR_COMMAND_LIMIT, trajectory_views
    from heguang_code.schema import Message
    command = "x" * (PAIR_COMMAND_LIMIT + 1)
    messages = [Message(role="assistant", tool_calls=[{"id": "large", "function": {
        "arguments": json.dumps({"command": command})}}]),
        Message(role="tool", content="output", tool_call_id="large")]
    views = list(trajectory_views(messages))
    assert command in views[0][0]
    assert views[1] == ("output", "content", None)


def test_corrupt_persisted_command_rejects_output_without_a_partial_commit(tmp_path):
    import pytest
    from heguang_code.schema import AddRequest
    from heguang_code.store import Store
    store = Store(Settings(data_dir=tmp_path, api_key="test"))
    store.add(AddRequest(user_id="u", request_id="parent", session_id="s", messages=[
        {"role": "assistant", "tool_calls": [{"id": "c", "function": {"arguments": '{"command":"real_command"}'}}]}]))
    db = store.connect("u")
    db.execute("UPDATE call_state SET command=?", (b"damaged",))
    db.commit()
    db.close()
    with pytest.raises(RuntimeError, match="Corrupt command"):
        store.add(AddRequest(user_id="u", request_id="output", session_id="s", messages=[
            {"role": "tool", "content": "uncommitted output", "tool_call_id": "c"}]))
    db = store.connect("u", create=False)
    assert db.execute("SELECT count(*) FROM sources").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM writes WHERE request_id='output'").fetchone()[0] == 0
    db.close()
