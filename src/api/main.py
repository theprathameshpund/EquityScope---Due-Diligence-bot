"""FastAPI app factory with lifespan and health checks."""

from __future__ import annotations

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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    log.info("api_starting", environment=settings.environment)
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
        try:
            async with await psycopg.AsyncConnection.connect(
                settings.postgres_dsn, connect_timeout=3
            ) as conn:
                await conn.execute("SELECT 1")
                postgres_ok = True
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
