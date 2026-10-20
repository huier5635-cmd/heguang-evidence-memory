"""Synthetic HTTP contract check, independent of official Smoke/Full quotas."""
import argparse
import json
import os
import time
import urllib.error
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8750")
    parser.add_argument("--key-env", default="AML_API_KEY")
    args = parser.parse_args()
    key = os.getenv(args.key_env)
    if not key:
        parser.error(f"Set {args.key_env} locally; no keys in URLs or command arguments")

    def call(path, body=None):
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(args.base_url.rstrip("/") + path, data=data, headers=headers)
        with urllib.request.urlopen(request, timeout=120) as response:
            assert response.status == 200
            return json.load(response)

    health = call("/health")
    run = uuid.uuid4().hex
    a, b = "preflight:" + run + ":A", "preflight:" + run + ":B"
    started = time.monotonic()
    body = {"request_id": run, "user_id": a, "session_id": "session-A",
            "messages": [{"role": "user", "content": "Synthetic test: the copper bird visits Lake Mere on Tuesday."}]}
    expected = {"success": True, "request_id": run, "user_id": a, "session_id": "session-A"}
    assert call("/add", body) == expected
    query = {"query": "Where does the copper bird visit?", "user_id": a, "top_k": 1}
    first = call("/search", query)
    assert len(first["data"]) == 1 and "Lake Mere" in first["data"][0]["content"]
    assert call("/add", body) == expected
    assert call("/search", query) == first
    assert call("/search", {**query, "user_id": b}) == {"data": []}
    print(json.dumps({"status": "pass", "health": health, "elapsed_seconds": round(time.monotonic() - started, 3),
                      "checks": ["add_echo", "immediate_search", "top_k", "idempotent_retry", "user_isolation"],
                      "official_evaluation": False}, indent=2))


if __name__ == "__main__":
    main()

