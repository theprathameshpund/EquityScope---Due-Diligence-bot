"""LangGraph supervisor: graph definition, routing, budget, progress events.

Topology:
    ingest_check → [filings, market, news] in parallel → analyst → writer
    → critic → (revise loop back to writer, max CRITIC_MAX_REVISIONS)
    → finalize

CLI:  python -m app.agents.orchestrator AAPL "competition risk"
      python -m app.agents.orchestrator --resume <run_id>
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

import app.report.schema  # noqa: F401  (finalizes AgentState forward refs)
from app.agents.analyst_agent import analyst_node
from app.agents.critic_agent import critic_node
from app.agents.filings_agent import filings_node
from app.agents.market_agent import market_node
from app.agents.news_agent import news_node
from app.agents.writer_agent import writer_node
from app.config import settings
from app.llm.router import get_tracker
from app.logging_setup import bind_run_id, get_logger
from app.memory.checkpoints import get_checkpointer
from app.rag.ingestion import ingest_company
from app.report.render import render_markdown
from app.report.schema import CompanyMeta, DDReport, ReportMetadata
from app.state import AgentState, ProgressEvent, RetrievedEvidence, RunBudget, new_id

log = get_logger(__name__)

EVENTS_CHANNEL = "equityscope:events:{run_id}"
EVENTS_LIST = "equityscope:eventlog:{run_id}"
RUN_KEY = "equityscope:run:{run_id}"
RUN_TTL_S = 7 * 24 * 3600

REPORTS_DIR = Path("data/reports")


# ── Redis progress events (graceful when Redis is down) ───────


@lru_cache(maxsize=1)
def _redis() -> Any | None:
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        return client
    except Exception as exc:
        log.warning("redis_unavailable_events_disabled", error=str(exc))
        return None


EventStatus = Literal["start", "end", "error", "warning", "done"]


def publish_event(run_id: str, node: str, status: EventStatus, message: str = "") -> None:
    tracker = get_tracker(run_id)
    event = ProgressEvent(
        run_id=run_id,
        node=node,
        status=status,
        message=message,
        tokens_used=tracker.tokens_used,
        cost_usd=round(tracker.cost_usd, 6),
    )
    client = _redis()
    if client is None:
        return
    try:
        payload = event.model_dump_json()
        client.publish(EVENTS_CHANNEL.format(run_id=run_id), payload)
        key = EVENTS_LIST.format(run_id=run_id)
        client.rpush(key, payload)
        client.expire(key, RUN_TTL_S)
    except Exception as exc:
        log.warning("event_publish_failed", error=str(exc))


def store_run_status(
    run_id: str,
    status: str,
    *,
    company: str = "",
    report: DDReport | None = None,
    error: str | None = None,
    evidence: list[RetrievedEvidence] | None = None,
) -> None:
    client = _redis()
    if client is None:
        return
    payload: dict[str, Any] = {
        "run_id": run_id,
        "status": status,
        "company": company,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if report is not None:
        payload["report"] = report.model_dump(mode="json")
        payload["markdown"] = render_markdown(report)
    if evidence:
        # Cited chunks ride along so the UI can show source text + EDGAR link.
        payload["evidence"] = [e.model_dump(mode="json") for e in evidence]
    if error:
        payload["error"] = error
    try:
        client.set(RUN_KEY.format(run_id=run_id), json.dumps(payload), ex=RUN_TTL_S)
    except Exception as exc:
        log.warning("run_status_store_failed", error=str(exc))


# ── Nodes ──────────────────────────────────────────────────────


def ingest_check_node(state: AgentState) -> dict[str, Any]:
    """Resolve the company and make sure its filings are indexed."""
    identity, n_chunks = ingest_company(state.company_input)
    log.info("ingest_check_done", ticker=identity.ticker, new_chunks=n_chunks)
    return {
        "ticker": identity.ticker,
        "cik": identity.cik,
        "company_name": identity.name,
        "status": "ingested",
    }


def finalize_node(state: AgentState) -> dict[str, Any]:
    """Drop still-failing claims, fill metadata, persist the final report."""
    tracker = get_tracker(state.run_id)
    new_gaps: list[str] = []

    report = state.report
    if report is None:
        # Budget abort or writer failure — ship a partial, honest report.
        report = DDReport(
            company=CompanyMeta(
                name=state.company_name or state.company_input,
                ticker=state.ticker,
                cik=state.cik,
            ),
            metadata=ReportMetadata(run_id=state.run_id),
        )
        new_gaps.append("Report generation did not complete; partial output only.")

    for claim in report.all_claims():
        if claim.verification_status in {"unsupported", "unverified"} and (
            claim.verification_status == "unsupported"
        ):
            report.drop_claim(claim.claim_id)
            gap = f"Unverifiable claim dropped after max revisions: {claim.text[:120]}"
            new_gaps.append(gap)
            log.warning("unverified_claim_dropped", claim_id=claim.claim_id,
                        text=claim.text[:120])

    duration = (datetime.now(UTC) - state.started_at).total_seconds()
    warnings = list(new_gaps)
    if tracker.exceeded:
        warnings.append(
            f"Token budget exceeded ({tracker.tokens_used}/{tracker.token_limit}); "
            "report may be partial."
        )

    report.data_gaps = list(dict.fromkeys([*state.data_gaps, *new_gaps]))
    report.metadata = ReportMetadata(
        run_id=state.run_id,
        duration_s=round(duration, 1),
        tokens_used=tracker.tokens_used,
        cost_usd=round(tracker.cost_usd, 6),
        model_versions={
            "fast": settings.groq_model_fast,
            "smart": settings.groq_model_smart,
            "writer": settings.anthropic_model
            if settings.llm_writer_provider == "anthropic"
            else settings.groq_model_smart,
            "embeddings": settings.embedding_model,
            "nli": settings.critic_nli_model,
        },
        warnings=warnings,
    )

    budget = RunBudget(
        token_limit=tracker.token_limit,
        tokens_used=tracker.tokens_used,
        cost_usd=round(tracker.cost_usd, 6),
    )
    return {"report": report, "budget": budget, "data_gaps": new_gaps, "status": "done"}


def route_after_critic(state: AgentState) -> Literal["writer", "finalize"]:
    """Supervisor decision: revise rejected claims or finalize."""
    if state.report is None:
        return "finalize"
    tracker = get_tracker(state.run_id)
    has_failures = any(
        c.verification_status == "unsupported" for c in state.report.all_claims()
    )
    if has_failures and state.revision_count < settings.critic_max_revisions:
        if tracker.exceeded:
            log.warning("revision_skipped_budget_exceeded")
            return "finalize"
        return "writer"
    return "finalize"


def _with_events(
    name: str, fn: Callable[[AgentState], dict[str, Any]]
) -> Callable[..., dict[str, Any]]:
    def wrapped(state: AgentState) -> dict[str, Any]:
        bind_run_id(state.run_id)
        publish_event(state.run_id, name, "start")
        try:
            updates = fn(state)
        except Exception as exc:
            publish_event(state.run_id, name, "error", message=str(exc)[:300])
            raise
        publish_event(state.run_id, name, "end")
        return updates

    return wrapped


# ── Graph ──────────────────────────────────────────────────────


def build_graph(checkpointer: Any = None) -> Any:
    graph = StateGraph(AgentState)
    graph.add_node("ingest_check", _with_events("ingest_check", ingest_check_node))
    graph.add_node("filings", _with_events("filings", filings_node))
    graph.add_node("market", _with_events("market", market_node))
    graph.add_node("news", _with_events("news", news_node))
    graph.add_node("analyst", _with_events("analyst", analyst_node))
    graph.add_node("writer", _with_events("writer", writer_node))
    graph.add_node("critic", _with_events("critic", critic_node))
    graph.add_node("finalize", _with_events("finalize", finalize_node))

    graph.add_edge(START, "ingest_check")
    graph.add_edge("ingest_check", "filings")
    graph.add_edge("ingest_check", "market")
    graph.add_edge("ingest_check", "news")
    graph.add_edge(["filings", "market", "news"], "analyst")
    graph.add_edge("analyst", "writer")
    graph.add_edge("writer", "critic")
    graph.add_conditional_edges(
        "critic", route_after_critic, {"writer": "writer", "finalize": "finalize"}
    )
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)


# ── Entry points ───────────────────────────────────────────────


def run_report(
    company: str,
    focus: str = "",
    run_id: str | None = None,
    resume: bool = False,
) -> tuple[str, DDReport | None]:
    """Execute (or resume) a full due diligence run. Returns (run_id, report)."""
    rid = run_id or new_id("run")
    bind_run_id(rid)
    get_tracker(rid)  # initialize the budget tracker for this run
    store_run_status(rid, "running", company=company)
    publish_event(rid, "run", "start", message=f"Run started for {company}")

    try:
        with get_checkpointer() as checkpointer:
            graph = build_graph(checkpointer)
            config = {"configurable": {"thread_id": rid}}
            graph_input: AgentState | None = None
            if not resume:
                graph_input = AgentState(
                    run_id=rid,
                    company_input=company,
                    focus=focus,
                    budget=RunBudget(token_limit=settings.run_token_budget),
                )
            final: dict[str, Any] = graph.invoke(graph_input, config)
    except Exception as exc:
        log.error("run_failed", error=str(exc))
        store_run_status(rid, "failed", company=company, error=str(exc))
        publish_event(rid, "run", "error", message=str(exc)[:300])
        raise

    report = final.get("report")
    assert report is None or isinstance(report, DDReport)
    evidence = [e for e in final.get("evidence", []) if isinstance(e, RetrievedEvidence)]
    store_run_status(rid, "done", company=company, report=report, evidence=evidence)
    publish_event(rid, "run", "done", message="Report ready")
    return rid, report


def save_report(run_id: str, report: DDReport) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORTS_DIR / f"{run_id}.json"
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    md_path = REPORTS_DIR / f"{run_id}.md"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path


def main(argv: list[str]) -> int:
    if not argv:
        print(
            "usage: python -m src.agents.orchestrator <company> [focus]\n"
            "       python -m src.agents.orchestrator --resume <run_id>"
        )
        return 2

    if argv[0] == "--resume":
        if len(argv) < 2:
            print("--resume requires a run_id")
            return 2
        run_id, report = run_report("", resume=True, run_id=argv[1])
    else:
        company = argv[0]
        focus = argv[1] if len(argv) > 1 else ""
        run_id, report = run_report(company, focus)

    if report is None:
        print(f"Run {run_id} finished without a report.")
        return 1
    path = save_report(run_id, report)
    print(f"Run {run_id} complete.")
    print(f"Report JSON: {path}")
    print(f"Report Markdown: {path.with_suffix('.md')}")
    print(
        f"Tokens: {report.metadata.tokens_used:,} · "
        f"Cost: ${report.metadata.cost_usd:.4f} · "
        f"Claims: {len(report.all_claims())}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
