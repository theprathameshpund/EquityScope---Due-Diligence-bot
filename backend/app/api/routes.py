"""API routes: report creation, status, SSE progress stream."""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from typing import cast

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from app.agents.orchestrator import (
    EVENTS_CHANNEL,
    EVENTS_LIST,
    REPORTS_DIR,
    RUN_KEY,
    get_memory_event_log,
    get_memory_run_status,
    list_memory_run_statuses,
    store_run_status,
    run_report,
    save_report,
)
from app.api.schemas import (
    CreateReportRequest,
    CreateReportResponse,
    ReportStatusResponse,
    RunListResponse,
    RunSummary,
)
from app.config import settings
from app.logging_setup import get_logger
from app.state import new_id

log = get_logger(__name__)

router = APIRouter(prefix="/api")

# In-memory rate limiting: 5 report runs per hour per IP (production only).
RATE_LIMIT_RUNS = 5
RATE_LIMIT_WINDOW_S = 3600
_request_log: dict[str, deque[float]] = defaultdict(deque)


def _company_label_from_report(report: dict[str, object]) -> str:
    company = report.get("company")
    if not isinstance(company, dict):
        return str(company or "")
    ticker = str(company.get("ticker") or "").strip()
    name = str(company.get("name") or "").strip()
    if ticker and name:
        return f"{ticker} - {name}"
    return ticker or name


def _run_summary_from_saved_report(path: object) -> RunSummary | None:
    try:
        from pathlib import Path

        report_path = Path(path)
        raw = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        run_id = str(metadata.get("run_id") or report_path.stem)
        updated_at = str(metadata.get("generated_at") or report_path.stat().st_mtime)
        return RunSummary(
            run_id=run_id,
            status="done",
            company=_company_label_from_report(raw),
            updated_at=updated_at,
            cost_usd=metadata.get("cost_usd"),
            tokens_used=metadata.get("tokens_used"),
        )
    except Exception as exc:
        log.warning("saved_report_summary_failed", path=str(path), error=str(exc))
        return None


def _saved_report_summaries() -> list[RunSummary]:
    if not REPORTS_DIR.exists():
        return []
    runs: list[RunSummary] = []
    for path in REPORTS_DIR.glob("*.json"):
        summary = _run_summary_from_saved_report(path)
        if summary is not None:
            runs.append(summary)
    return runs


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
    store_run_status(run_id, "queued", company=payload.company)
    background.add_task(asyncio.to_thread, _execute_run, run_id, payload.company, payload.focus)
    log.info("run_queued", run_id=run_id, company=payload.company, ip=client_ip)
    return CreateReportResponse(run_id=run_id, status="queued")


@router.get("/reports", response_model=RunListResponse)
async def list_reports() -> RunListResponse:
    """Recent runs (newest first) so the UI can reopen past reports."""
    if settings.runtime_storage == "local":
        # Merge persisted reports with in-memory statuses. The previous code
        # returned only memory when one current-session run existed, hiding all
        # saved reports from data/reports.
        by_id: dict[str, RunSummary] = {
            run.run_id: run for run in _saved_report_summaries()
        }
        for data in list_memory_run_statuses():
            metadata = (data.get("report") or {}).get("metadata") or {}
            run_id = str(data.get("run_id", ""))
            if not run_id:
                continue
            by_id[run_id] = RunSummary(
                run_id=run_id,
                status=str(data.get("status", "unknown")),
                company=str(data.get("company", "")) or by_id.get(run_id, RunSummary(run_id=run_id, status="unknown")).company,
                updated_at=str(data.get("updated_at", "")) or by_id.get(run_id, RunSummary(run_id=run_id, status="unknown")).updated_at,
                cost_usd=metadata.get("cost_usd", by_id.get(run_id).cost_usd if run_id in by_id else None),
                tokens_used=metadata.get("tokens_used", by_id.get(run_id).tokens_used if run_id in by_id else None),
            )
        runs = list(by_id.values())
        runs.sort(key=lambda r: r.updated_at, reverse=True)
        return RunListResponse(runs=runs[:50])

    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
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
    except Exception as exc:
        log.warning("redis_unavailable_list_reports", error=str(exc))
        runs = [
            RunSummary(
                run_id=str(data.get("run_id", "")),
                status=str(data.get("status", "unknown")),
                company=str(data.get("company", "")),
                updated_at=str(data.get("updated_at", "")),
                cost_usd=((data.get("report") or {}).get("metadata") or {}).get("cost_usd"),
                tokens_used=((data.get("report") or {}).get("metadata") or {}).get("tokens_used"),
            )
            for data in list_memory_run_statuses()
        ]
        runs.sort(key=lambda r: r.updated_at, reverse=True)
        return RunListResponse(runs=runs[:50])


@router.get("/reports/{run_id}", response_model=ReportStatusResponse)
async def get_report(run_id: str) -> ReportStatusResponse:
    if settings.runtime_storage == "local":
        data = get_memory_run_status(run_id)
        if data is None:
            # Try to load a saved report from disk when in-memory state is gone.
            try:
                path = REPORTS_DIR / f"{run_id}.json"
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and raw.get("report") is None:
                    # Persisted JSON stores the raw DDReport object.
                    company_data = raw.get("company", {})
                    company_text = ""
                    if isinstance(company_data, dict):
                        company_text = str(
                            company_data.get("ticker")
                            or company_data.get("name")
                            or raw.get("metadata", {}).get("company", "")
                        )
                    else:
                        company_text = str(company_data)
                    markdown_path = REPORTS_DIR / f"{run_id}.md"
                    markdown = None
                    if markdown_path.exists():
                        markdown = markdown_path.read_text(encoding="utf-8")
                    return ReportStatusResponse(
                        run_id=run_id,
                        status="done",
                        company=company_text,
                        report=raw,
                        markdown=markdown,
                        evidence=None,
                        error=None,
                    )
                return ReportStatusResponse(
                    run_id=run_id,
                    status=str(raw.get("status", "done")),
                    company=str(raw.get("company", "")),
                    report=raw.get("report"),
                    markdown=None,
                    evidence=None,
                    error=None,
                )
            except FileNotFoundError:
                return ReportStatusResponse(run_id=run_id, status="queued")
            except Exception as exc:
                log.warning("report_load_failed", run_id=run_id, error=str(exc))
                return ReportStatusResponse(run_id=run_id, status="queued")
        return ReportStatusResponse(
            run_id=run_id,
            status=str(data.get("status", "unknown")),
            company=str(data.get("company", "")),
            report=data.get("report"),
            markdown=data.get("markdown"),
            evidence=data.get("evidence"),
            error=data.get("error"),
        )

    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        try:
            raw = await client.get(RUN_KEY.format(run_id=run_id))
        finally:
            await client.aclose()
    except Exception as exc:
        log.warning("redis_unavailable_get_report", run_id=run_id, error=str(exc))
        data = get_memory_run_status(run_id)
        if data is None:
            return ReportStatusResponse(
                run_id=run_id,
                status="queued",
                error="Redis is unavailable; run status cannot be read right now.",
            )
        return ReportStatusResponse(
            run_id=run_id,
            status=str(data.get("status", "unknown")),
            company=str(data.get("company", "")),
            report=data.get("report"),
            markdown=data.get("markdown"),
            evidence=data.get("evidence"),
            error=data.get("error"),
        )
    if raw is None:
        # The run may be queued but not yet started.
        data = get_memory_run_status(run_id)
        if data is None:
            return ReportStatusResponse(run_id=run_id, status="queued")
        return ReportStatusResponse(
            run_id=run_id,
            status=str(data.get("status", "unknown")),
            company=str(data.get("company", "")),
            report=data.get("report"),
            markdown=data.get("markdown"),
            evidence=data.get("evidence"),
            error=data.get("error"),
        )
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
    if settings.runtime_storage == "local":
        seen = 0
        idle_deadline = time.monotonic() + 1800
        while time.monotonic() < idle_deadline:
            rows = get_memory_event_log(run_id, seen)
            for raw in rows:
                seen += 1
                yield {"event": "progress", "data": raw}
                if _is_terminal(raw):
                    return
            await asyncio.sleep(1.0)
        return

    import redis.asyncio as aioredis
    client = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=1.0,
        socket_timeout=1.0,
    )
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
    except Exception as exc:
        log.warning("redis_unavailable_event_stream", run_id=run_id, error=str(exc))
        seen = 0
        idle_deadline = time.monotonic() + 1800
        while time.monotonic() < idle_deadline:
            rows = get_memory_event_log(run_id, seen)
            for raw in rows:
                seen += 1
                yield {"event": "progress", "data": raw}
                if _is_terminal(raw):
                    return
            await asyncio.sleep(1.0)
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
