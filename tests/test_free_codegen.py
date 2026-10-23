import json

import httpx
import pytest

from research.free_codegen import FreeOnlyClient, run_plan


def test_changed_free_model_price_blocks_inference_before_credentials_are_sent():
    calls = []
    def handler(request):
        calls.append(request)
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"data": [{"id": "test/code:free", "pricing": {"prompt": "0.01", "completion": "0"}}]})
    client = FreeOnlyClient("private-test-key", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ValueError, match="zero"):
            client.generate("test/code:free", [{"role": "user", "content": "public source"}])
        assert len(calls) == 1 and calls[0].url.path == "/api/v1/models"
    finally:
        client.close()


def test_free_request_caps_every_text_price_and_disables_fallback():
    calls = []
    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "test/code:free", "pricing": {"prompt": "0", "completion": "0", "request": "0"}}]})
        body = json.loads(request.content)
        assert body["provider"] == {"max_price": {"prompt": 0, "completion": 0, "request": 0},
                                    "allow_fallbacks": False, "data_collection": "deny"}
        assert body["model"] == "test/code:free" and body["max_tokens"] == 4096
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"edits":[]}'}}], "usage": {"cost": 0}})
    client = FreeOnlyClient("private-test-key", transport=httpx.MockTransport(handler))
    try:
        result = client.generate("test/code:free", [{"role": "user", "content": "public source"}])
        assert result["reported_cost_zero"] and len(calls) == 2
    finally:
        client.close()


def test_rate_limit_stops_without_retry_or_key_in_error():
    posts = []
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "test/code:free", "pricing": {"prompt": "0", "completion": "0"}}]})
        posts.append(request)
        return httpx.Response(429, json={"error": "quota exceeded"})
    client = FreeOnlyClient("private-test-key", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeError, match="429") as error:
            client.generate("test/code:free", [{"role": "user", "content": "public source"}])
        assert "private-test-key" not in str(error.value) and len(posts) == 1
    finally:
        client.close()


def test_missing_cost_stops_before_second_arm(monkeypatch, tmp_path):
    calls = []
    class UnknownCost:
        def __init__(self, key):
            pass
        def generate(self, model, messages):
            calls.append(messages)
            return {"response": {"choices": []}, "reported_cost_zero": False}
        def close(self):
            pass
    monkeypatch.setattr("research.free_codegen.FreeOnlyClient", UnknownCost)
    plan = {"public_inputs_verified": True, "official_data": False, "arm_order": ["with_memory", "without_memory"],
            "messages": {"with_memory": ["public history"], "without_memory": ["public task"]}}
    with pytest.raises(RuntimeError, match="incomplete"):
        run_plan(plan, "test/code:free", "test", tmp_path / "drafts")
    assert len(calls) == 1
