"""API routes: report creation, status, SSE progress stream."""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from typing import cast

import redis.asyncio as aioredis
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from src.agents.orchestrator import (
    EVENTS_CHANNEL,
    EVENTS_LIST,
    RUN_KEY,
    run_report,
    save_report,
)
from src.api.schemas import (
    CreateReportRequest,
    CreateReportResponse,
    ReportStatusResponse,
    RunListResponse,
    RunSummary,
)
from src.config import settings
from src.logging_setup import get_logger
from src.state import new_id

log = get_logger(__name__)

router = APIRouter(prefix="/api")

# In-memory rate limiting: 5 report runs per hour per IP (production only).
RATE_LIMIT_RUNS = 5
RATE_LIMIT_WINDOW_S = 3600
_request_log: dict[str, deque[float]] = defaultdict(deque)


def _check_rate_limit(client_ip: str) -> None:
    if settings.environment != "production":
        return
    now = time.monotonic()
    window = _request_log[client_ip]
    while window and now - window[0] > RATE_LIMIT_WINDOW_S:
        window.popleft()
    if len(window) >= RATE_LIMIT_RUNS:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit: max {RATE_LIMIT_RUNS} report runs per hour per IP.",
        )
    window.append(now)


def _execute_run(run_id: str, company: str, focus: str) -> None:
    """Background task: run the full graph (sync, in a worker thread)."""
    try:
        _, report = run_report(company, focus, run_id=run_id)
        if report is not None:
            save_report(run_id, report)
    except Exception as exc:
        log.error("background_run_failed", run_id=run_id, error=str(exc))


@router.post("/reports", response_model=CreateReportResponse, status_code=202)
async def create_report(
    payload: CreateReportRequest, request: Request, background: BackgroundTasks
) -> CreateReportResponse:
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)
    run_id = new_id("run")
    background.add_task(asyncio.to_thread, _execute_run, run_id, payload.company, payload.focus)
    log.info("run_queued", run_id=run_id, company=payload.company, ip=client_ip)
    return CreateReportResponse(run_id=run_id, status="queued")


@router.get("/reports", response_model=RunListResponse)
async def list_reports() -> RunListResponse:
    """Recent runs (newest first) so the UI can reopen past reports."""
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    runs: list[RunSummary] = []
    try:
        async for key in client.scan_iter("equityscope:run:*"):
            raw = await client.get(key)
            if raw is None:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            metadata = (data.get("report") or {}).get("metadata") or {}
            runs.append(
                RunSummary(
                    run_id=str(data.get("run_id", "")),
                    status=str(data.get("status", "unknown")),
                    company=str(data.get("company", "")),
                    updated_at=str(data.get("updated_at", "")),
                    cost_usd=metadata.get("cost_usd"),
                    tokens_used=metadata.get("tokens_used"),
                )
            )
    finally:
        await client.aclose()
    runs.sort(key=lambda r: r.updated_at, reverse=True)
    return RunListResponse(runs=runs[:50])


@router.get("/reports/{run_id}", response_model=ReportStatusResponse)
async def get_report(run_id: str) -> ReportStatusResponse:
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        raw = await client.get(RUN_KEY.format(run_id=run_id))
    finally:
        await client.aclose()
    if raw is None:
        # The run may be queued but not yet started.
        return ReportStatusResponse(run_id=run_id, status="queued")
    data = json.loads(raw)
    return ReportStatusResponse(
        run_id=run_id,
        status=str(data.get("status", "unknown")),
        company=str(data.get("company", "")),
        report=data.get("report"),
        markdown=data.get("markdown"),
        evidence=data.get("evidence"),
        error=data.get("error"),
    )


async def _event_stream(run_id: str) -> AsyncIterator[dict[str, str]]:
    """Replay logged events, then follow live pub/sub until the run ends."""
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(EVENTS_CHANNEL.format(run_id=run_id))
        seen = 0
        backlog = cast("list[str]", await client.lrange(EVENTS_LIST.format(run_id=run_id), 0, -1))
        for raw in backlog:
            seen += 1
            yield {"event": "progress", "data": raw}
            if _is_terminal(raw):
                return

        idle_deadline = time.monotonic() + 1800  # 30 min hard cap
        while time.monotonic() < idle_deadline:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2.0)
            if message is None:
                # Catch up from the log in case pub/sub dropped anything.
                rows = cast(
                    "list[str]",
                    await client.lrange(EVENTS_LIST.format(run_id=run_id), seen, -1),
                )
                for raw in rows:
                    seen += 1
                    yield {"event": "progress", "data": raw}
                    if _is_terminal(raw):
                        return
                continue
            raw = str(message["data"])
            seen += 1
            yield {"event": "progress", "data": raw}
            if _is_terminal(raw):
                return
    finally:
        await pubsub.aclose()  # type: ignore[no-untyped-call]
        await client.aclose()


def _is_terminal(raw_event: str) -> bool:
    try:
        data = json.loads(raw_event)
    except json.JSONDecodeError:
        return False
    return data.get("node") == "run" and data.get("status") in {"done", "error"}


@router.get("/reports/{run_id}/events")
async def report_events(run_id: str) -> EventSourceResponse:
    return EventSourceResponse(_event_stream(run_id))
