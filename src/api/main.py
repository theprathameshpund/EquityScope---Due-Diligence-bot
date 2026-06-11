"""FastAPI app factory with lifespan and health checks."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import router
from src.api.schemas import HealthResponse
from src.config import settings
from src.logging_setup import get_logger, setup_logging

log = get_logger(__name__)


async def _fail_orphaned_runs() -> None:
    """Runs execute in this process; anything still 'running' at startup was
    killed by a restart. Mark it failed so the UI never waits forever."""
    try:
        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            async for key in client.scan_iter("equityscope:run:*"):
                raw = await client.get(key)
                if raw is None:
                    continue
                data = json.loads(raw)
                if data.get("status") == "running":
                    run_id = str(data.get("run_id", ""))
                    data["status"] = "failed"
                    data["error"] = (
                        "API restarted while this run was in progress. Resume it with: "
                        f"python -m src.agents.orchestrator --resume {run_id}"
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
    yield
    log.info("api_stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="EquityScope",
        description="Multi-agent financial due diligence copilot",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.environment == "development" else [],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        qdrant_ok = postgres_ok = redis_ok = False
        try:
            import httpx

            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{settings.qdrant_url}/readyz")
                qdrant_ok = resp.status_code == 200
        except Exception as exc:
            log.warning("healthz_qdrant_failed", error=str(exc))
        def _check_postgres() -> bool:
            # Sync connect in a thread: psycopg async mode is incompatible
            # with the Proactor event loop uvicorn uses on Windows.
            with psycopg.connect(settings.postgres_dsn, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            return True

        try:
            postgres_ok = await asyncio.to_thread(_check_postgres)
        except Exception as exc:
            log.warning("healthz_postgres_failed", error=str(exc))
        try:
            client_r = aioredis.from_url(settings.redis_url, decode_responses=True)
            try:
                redis_ok = bool(await client_r.ping())
            finally:
                await client_r.aclose()
        except Exception as exc:
            log.warning("healthz_redis_failed", error=str(exc))

        all_ok = qdrant_ok and postgres_ok and redis_ok
        return HealthResponse(
            status="ok" if all_ok else "degraded",
            qdrant=qdrant_ok,
            postgres=postgres_ok,
            redis=redis_ok,
        )

    return app


app = create_app()
