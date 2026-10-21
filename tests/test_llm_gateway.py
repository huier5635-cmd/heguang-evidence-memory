import asyncio
import json

import httpx
import numpy as np
import pytest

from aml_memory.config import Settings
from aml_memory.engine import Engine
from aml_memory.models import AcademicModels, ModelUnavailable
from aml_memory.schema import AddRequest

GATEWAY = "https://ai-gateway.vercel.sh/v1"
SOURCE = "Ada owns a silver telescope."


def completion(model="gpt-4o-mini-2024-07-18"):
    return {"model": model, "choices": [{"message": {"content": json.dumps({"entries": [
        {"quote": SOURCE, "tags": ["astronomy"]}]})}}]}


@pytest.mark.parametrize("provider,base,reported", [
    ("openai", "https://api.openai.com/v1", "gpt-4o-mini-2024-07-18"),
    ("vercel", GATEWAY, "gpt-4o-mini-2024-07-18"),
    ("vercel", GATEWAY + "/", "openai/gpt-4o-mini")])
def test_model_identity_and_gateway_wire_protocol(tmp_path, provider, base, reported):
    async def exercise():
        settings = Settings(data_dir=tmp_path, api_key="test", llm_key="mock-only", llm_provider=provider, llm_url=base)
        models = AcademicModels(settings)
        seen = []
        async def handler(request):
            payload = json.loads(request.content)
            seen.append(payload)
            assert str(request.url) == base.rstrip("/") + "/chat/completions"
            assert request.headers["authorization"] == "Bearer mock-only"
            assert payload["response_format"] == {"type": "json_object"}
            assert payload["temperature"] == 0 and payload["max_tokens"] == 1500
            assert "JSON" in payload["messages"][0]["content"]
            if provider == "vercel":
                assert payload["model"] == "openai/gpt-4o-mini"
                assert payload["providerOptions"] == {"gateway": {"only": ["openai"]}}
                assert payload["stream"] is False
            else:
                assert payload["model"] == "gpt-4o-mini" and "providerOptions" not in payload
            return httpx.Response(200, json=completion(reported))
        await models.http.aclose()
        models.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            assert await models.source_tags(SOURCE) == [{"quote": SOURCE, "tags": ["astronomy"]}]
            assert len(seen) == 1
            assert models.EMBEDDING_MODEL == "text-embedding-v4" and models.LLM_MODEL == "gpt-4o-mini"
        finally:
            await models.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("reported", [None, "gpt-5-mini", "openai/gpt-4o"])
def test_gateway_rejects_other_or_missing_model_identity_without_committing(tmp_path, reported):
    async def exercise():
        engine = Engine(Settings(data_dir=tmp_path, api_key="test", mode="research", embedding_key="mock-only",
                                 llm_key="mock-only", llm_provider="vercel", llm_url=GATEWAY))
        async def handler(request):
            return httpx.Response(200, json=completion(reported))
        await engine.models.http.aclose()
        engine.models.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async def unexpected_embeddings(texts):
            raise AssertionError("Embedding must not start after an invalid LLM identity")
        engine.models.embed = unexpected_embeddings
        try:
            with pytest.raises(ModelUnavailable, match="identity"):
                await engine.add(AddRequest(request_id="r", user_id="u", session_id="s",
                                            messages=[{"role": "user", "content": SOURCE}]))
            db = engine.store.connect("u")
            assert db.execute("SELECT count(*) FROM writes").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM docs").fetchone()[0] == 0
            db.close()
        finally:
            await engine.models.close()
    asyncio.run(exercise())


def test_exhausted_credits_do_not_retry_or_expose_upstream_body(tmp_path):
    async def exercise():
        models = AcademicModels(Settings(data_dir=tmp_path, api_key="test", llm_key="mock-only",
                                         llm_provider="vercel", llm_url=GATEWAY))
        calls = []
        async def handler(request):
            calls.append(request.url.host)
            return httpx.Response(402, json={"error": "sensitive-upstream-body-should-not-leak"})
        await models.http.aclose()
        models.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(ModelUnavailable, match="HTTP 402") as failure:
                await models.source_tags(SOURCE)
            assert "sensitive-upstream" not in str(failure.value)
            assert calls == ["ai-gateway.vercel.sh"]
        finally:
            await models.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("base", ["https://api.openai.com/v1", "https://ai-gateway.vercel.sh.invalid/v1",
                                  "https://ai-gateway.vercel.sh/v1?other=1", "https://ai-gateway.vercel.sh/v2"])
def test_gateway_configuration_rejects_unexpected_endpoints(tmp_path, base):
    with pytest.raises(ValueError, match="official AI Gateway"):
        Settings(data_dir=tmp_path, api_key="test", llm_provider="vercel", llm_url=base)


def test_gateway_environment_defaults_to_official_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("AML_MODE", "lexical")
    monkeypatch.setenv("AML_API_KEY", "test")
    monkeypatch.setenv("AML_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AML_LLM_PROVIDER", "vercel")
    monkeypatch.delenv("AML_LLM_URL", raising=False)
    assert Settings.from_env().llm_url == GATEWAY


def test_research_index_is_bound_to_llm_route(tmp_path):
    async def exercise():
        old = Engine(Settings(data_dir=tmp_path, api_key="test", mode="research", embedding_key="mock-only", llm_key="mock-only"))
        async def tags(source):
            return []
        async def embeddings(texts):
            return [np.ones(1024, dtype=np.float32) / 32 for _ in texts]
        old.models.source_tags, old.models.embed = tags, embeddings
        req = AddRequest(request_id="r", user_id="u", session_id="s", messages=[{"role": "user", "content": SOURCE}])
        await old.add(req)
        await old.models.close()
        changed = Engine(Settings(data_dir=tmp_path, api_key="test", mode="research", embedding_key="mock-only",
                                  llm_key="mock-only", llm_provider="vercel", llm_url=GATEWAY))
        try:
            with pytest.raises(RuntimeError, match="different configuration"):
                await changed.add(req)
        finally:
            await changed.models.close()
    asyncio.run(exercise())
