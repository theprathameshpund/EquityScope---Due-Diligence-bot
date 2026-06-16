"""FastAPI app factory with lifespan and health checks."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.api.schemas import HealthResponse
from app.config import settings
from app.logging_setup import get_logger, setup_logging

log = get_logger(__name__)


async def _fail_orphaned_runs() -> None:
    """Runs execute in this process; anything still 'running' at startup was
    killed by a restart. Mark it failed so the UI never waits forever."""
    try:
        client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        try:
            async for key in client.scan_iter("equityscope:run:*"):
                raw = await client.get(key)
                if raw is None:
                    continue
                data = json.loads(raw)
                if data.get("status") in {"queued", "running"}:
                    run_id = str(data.get("run_id", ""))
                    data["status"] = "failed"
                    data["error"] = (
                        "API restarted before this run reached a terminal state. Resume it with: "
                        f"python -m app.agents.orchestrator --resume {run_id}"
                    )
                    await client.set(key, json.dumps(data), ex=7 * 24 * 3600)
                    log.warning("orphaned_run_marked_failed", run_id=run_id)
        finally:
            await client.aclose()
    except Exception as exc:
        log.warning("orphaned_run_scan_failed", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    log.info("api_starting", environment=settings.environment)
    
    await _fail_orphaned_runs()
    
    # Start embedding model pre-load in background (non-blocking)
    # This allows the API to accept requests immediately while the model loads
    def _background_preload_embeddings() -> None:
        try:
            from app.rag.embeddings import get_embedder, embedding_dim
            log.info("preloading_embedding_model", model=settings.embedding_model)
            get_embedder()  # triggers lazy load
            dim = embedding_dim()
            log.info("embedding_model_ready", dimension=dim)
        except Exception as exc:
            log.warning("embedding_model_preload_failed", error=str(exc))
    
    # Schedule pre-load as a background task (doesn't block startup)
    asyncio.create_task(asyncio.to_thread(_background_preload_embeddings))
    
    yield
    log.info("api_stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="EquityScope",
        description="Multi-agent financial due diligence copilot",
        version="0.1.0",
        lifespan=lifespan,
    )
    configured_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.environment == "development" else configured_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=2048)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_api_error", path=str(request.url.path), error=str(exc))
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "path": str(request.url.path)},
        )

    app.include_router(router)

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        """
        Hard 3-second overall timeout — this endpoint NEVER hangs.
        All three checks run in parallel via asyncio.gather.
        """
        import httpx

        async def _check_qdrant() -> bool:
            try:
                async with httpx.AsyncClient(timeout=1.5) as client:
                    r = await client.get(f"{settings.qdrant_url}/readyz")
                    return r.status_code in (200, 204)
            except Exception as exc:
                log.warning("healthz_qdrant_failed", error=str(exc))
                return False

        async def _check_postgres() -> bool:
            try:
                def _sync() -> bool:
                    import socket
                    socket.setdefaulttimeout(1.5)
                    with psycopg.connect(
                        settings.postgres_dsn, connect_timeout=1
                    ) as conn:
                        conn.execute("SELECT 1")
                    return True
                return await asyncio.wait_for(asyncio.to_thread(_sync), timeout=2.0)
            except Exception as exc:
                log.warning("healthz_postgres_failed", error=str(exc))
                return False

        async def _check_redis() -> bool:
            try:
                client_r = aioredis.from_url(
                    settings.redis_url, decode_responses=True,
                    socket_connect_timeout=1, socket_timeout=1,
                )
                try:
                    return bool(await asyncio.wait_for(client_r.ping(), timeout=1.5))
                finally:
                    await client_r.aclose()
            except Exception as exc:
                log.warning("healthz_redis_failed", error=str(exc))
                return False

        # Run all checks in parallel; hard outer cap = 3s total
        try:
            qdrant_ok, postgres_ok, redis_ok = await asyncio.wait_for(
                asyncio.gather(
                    _check_qdrant(),
                    _check_postgres(),
                    _check_redis(),
                    return_exceptions=False,
                ),
                timeout=3.0,
            )
        except asyncio.TimeoutError:
            log.warning("healthz_overall_timeout")
            qdrant_ok = postgres_ok = redis_ok = False
        except Exception as exc:
            log.warning("healthz_gather_failed", error=str(exc))
            qdrant_ok = postgres_ok = redis_ok = False

        all_ok = qdrant_ok and postgres_ok and redis_ok
        return HealthResponse(
            status="ok" if all_ok else "degraded",
            qdrant=qdrant_ok,
            postgres=postgres_ok,
            redis=redis_ok,
        )

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
    )
