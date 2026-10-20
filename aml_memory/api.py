import asyncio
import contextlib
import hmac
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from . import __version__
from .config import Settings
from .engine import Engine
from .models import ModelUnavailable
from .schema import AddRequest, SearchRequest


def create_app(settings=None):
    settings = settings or Settings.from_env()
    engine = Engine(settings)
    slots = asyncio.Semaphore(settings.max_concurrency)

    async def expire():
        while True:
            await asyncio.to_thread(engine.store.purge_expired, settings.retention_days)
            await asyncio.sleep(3600)

    @asynccontextmanager
    async def lifespan(app):
        # Delete expired evaluation data before serving any request.
        await asyncio.to_thread(engine.store.purge_expired, settings.retention_days)
        cleanup = asyncio.create_task(expire())
        yield
        cleanup.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup
        await engine.models.close()

    app = FastAPI(title="Heguang Evidence Memory", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine = engine

    @app.middleware("http")
    async def gate(request: Request, call_next):
        if request.url.path == "/health":
            # Health checks must not queue behind long synchronous Add requests.
            return await call_next(request)
        if request.url.path != "/health":
            if settings.api_key:
                auth = request.headers.get("authorization", "")
                supplied = request.headers.get("x-api-key", "")
                if auth.lower().startswith(("bearer ", "token ")):
                    supplied = auth.split(" ", 1)[1]
                if not hmac.compare_digest(supplied.encode(), settings.api_key.encode()):
                    return JSONResponse({"detail": {"reason": "Invalid API credential"}}, status_code=401)
            total = 0
            body = bytearray()
            async for chunk in request.stream():
                total += len(chunk)
                if total > settings.max_body_bytes:
                    return JSONResponse({"detail": {"reason": "Request exceeds configured 64 MiB limit"}}, status_code=413)
                body.extend(chunk)
            # Cache the checked body for FastAPI's JSON/Pydantic parser.
            request._body = bytes(body)
        async with slots:
            return await call_next(request)

    @app.exception_handler(ModelUnavailable)
    async def model_failure(request, exc):
        return JSONResponse({"detail": {"reason": str(exc)}}, status_code=503)

    @app.exception_handler(RuntimeError)
    async def storage_failure(request, exc):
        return JSONResponse({"detail": {"reason": "Index configuration mismatch or storage failure"}}, status_code=503)

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": __version__, "mode": settings.mode}

    @app.post("/add")
    async def add(req: AddRequest):
        try:
            await engine.add(req)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"reason": str(exc)}) from None
        return {"success": True, "request_id": req.request_id, "user_id": req.user_id, "session_id": req.session_id}

    @app.post("/search")
    async def search(req: SearchRequest):
        try:
            data = await engine.search(req)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"reason": str(exc)}) from None
        return {"data": data}

    return app

