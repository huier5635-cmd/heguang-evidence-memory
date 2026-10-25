import asyncio
import contextlib
import hmac
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__
from .config import Settings
from .schema import AddRequest, SearchRequest
from .reranker import MODEL_NAME, PairScorer, fit_budget, reorder
from .selection import MAX_SPAN_CHARACTERS, coalesce
from .store import ConflictingWrite, Store, digest


def create_app(settings=None, scorer=None):
    settings = settings or Settings.from_env()
    store = Store(settings)
    scorer = scorer or (PairScorer(settings.reranker_dir, threads=settings.reranker_threads) if settings.reranker_dir else None)
    if settings.source_selection == "coalesce" and scorer is None:
        raise ValueError("Source coalescing requires the configured reranker")
    slots = asyncio.Semaphore(settings.max_concurrency)
    large_slot = asyncio.Semaphore(1)
    locks = [asyncio.Lock() for _ in range(256)]

    async def expire():
        while True:
            await asyncio.sleep(3600)
            await asyncio.to_thread(store.purge_expired)

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(store.purge_expired)
        cleanup = asyncio.create_task(expire())
        try:
            yield
        finally:
            cleanup.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup

    app = FastAPI(title="Qianora Coding Memory — development candidate", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store

    @app.middleware("http")
    async def gate(request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        supplied = request.headers.get("x-api-key", "")
        if auth.lower().startswith(("bearer ", "token ")):
            supplied = auth.split(" ", 1)[1]
        if not hmac.compare_digest(supplied.encode(), settings.api_key.encode()):
            return JSONResponse({"detail": {"reason": "Invalid API credential"}}, status_code=401)
        length_header = request.headers.get("content-length")
        if length_header is not None:
            if not length_header.isascii() or not length_header.isdecimal() or len(length_header) > 20:
                return JSONResponse({"detail": {"reason": "Invalid Content-Length"}}, status_code=400)
            expected = int(length_header)
            if expected > settings.max_body_bytes:
                return JSONResponse({"detail": {"reason": "Request exceeds configured body limit"}}, status_code=413)
        else:
            expected = None
        # Unknown/chunked lengths also reserve the large slot. Acquire it before
        # a processing slot, so a waiting large request cannot block small work.
        async with contextlib.AsyncExitStack() as admission:
            if expected is None or expected > settings.large_body_bytes:
                await admission.enter_async_context(large_slot)
            await admission.enter_async_context(slots)
            body = bytearray()
            async for part in request.stream():
                if len(body) + len(part) > settings.max_body_bytes:
                    return JSONResponse({"detail": {"reason": "Request exceeds configured body limit"}}, status_code=413)
                if expected is not None and len(body) + len(part) > expected:
                    return JSONResponse({"detail": {"reason": "Request length differs from Content-Length"}}, status_code=400)
                body.extend(part)
            if expected is not None and len(body) != expected:
                return JSONResponse({"detail": {"reason": "Request length differs from Content-Length"}}, status_code=400)
            request._body = bytes(body)
            # The middleware outlives the handler. Release the mutable copy before
            # JSON parsing and durable storage allocate their own working memory.
            del body
            return await call_next(request)

    @app.exception_handler(ConflictingWrite)
    async def conflict(request, error):
        return JSONResponse({"detail": {"reason": "request_id already committed with a different payload"}}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def invalid_payload(request, error):
        # Default error responses echo invalid source values. Large values
        # duplicate the request again; unpaired surrogates can break rendering.
        return JSONResponse({"detail": {"reason": "Invalid request payload",
            "fields": [list(item["loc"]) for item in error.errors()[:16]]}}, status_code=422)

    @app.exception_handler(RuntimeError)
    @app.exception_handler(sqlite3.Error)
    async def storage_error(request, error):
        return JSONResponse({"detail": {"reason": "Configuration, storage or model failure"}}, status_code=503)

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": __version__, "track": "coding", "profile": settings.profile,
                "reranker": MODEL_NAME if scorer else None,
                "evidence_budget_characters": settings.rerank_response_chars if scorer else settings.response_chars,
                "max_processing_requests": settings.max_concurrency,
                "max_body_bytes": settings.max_body_bytes, "large_body_bytes": settings.large_body_bytes,
                "large_requests_processed_at_once": 1,
                "reranker_weight": settings.reranker_weight if scorer else None,
                "reranker_threads": settings.reranker_threads if scorer else None,
                "source_selection": settings.source_selection,
                "max_source_span_characters": MAX_SPAN_CHARACTERS if settings.source_selection == "coalesce" else None}

    def lock(user):
        return locks[int(digest(user)[:8], 16) % len(locks)]

    @app.post("/add")
    async def add(req: AddRequest):
        async with lock(req.user_id):
            await asyncio.to_thread(store.add, req)
        return {"success": True, "request_id": req.request_id, "user_id": req.user_id, "session_id": req.session_id}

    @app.post("/search")
    async def search(req: SearchRequest):
        async with lock(req.user_id):
            pool = req.model_copy(update={"top_k": 100}) if scorer else req
            data = await asyncio.to_thread(store.search, pool, selection_pool=scorer is not None)
            if scorer and data:
                def rerank():
                    prefix = data[:16]
                    scores = scorer.scores(req.query, [row["content"].split("\n", 1)[1] for row in prefix])
                    if len(scores) != len(prefix):
                        raise RuntimeError("Reranker result length differs from candidates")
                    ranked = reorder(data, scores, weight=settings.reranker_weight)
                    if settings.source_selection == "coalesce":
                        ranked = coalesce(ranked)
                    return fit_budget(ranked, top_k=req.top_k, characters=settings.rerank_response_chars)
                data = await asyncio.to_thread(rerank)
        return {"data": data}

    return app
