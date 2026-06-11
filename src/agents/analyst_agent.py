"""Analyst agent: deterministic XBRL metrics + LLM commentary that only
restates computed values (verified later by the critic)."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from src.llm.router import LLMRouter, load_prompt
from src.logging_setup import get_logger
from src.state import AgentState, Claim, FinancialAnalysis
from src.tools.metrics import compute_metrics
from src.tools.xbrl import fetch_financial_facts

log = get_logger(__name__)


class _CommentaryClaim(BaseModel):
    text: str
    metric_ids: list[str] = Field(default_factory=list)


class _Commentary(BaseModel):
    claims: list[_CommentaryClaim]


def _write_commentary(
    router: LLMRouter, state: AgentState, analysis: FinancialAnalysis
) -> list[Claim]:
    payload = {
        "metrics": [m.model_dump() for m in analysis.metrics],
        "anomalies": analysis.anomalies,
    }
    system = load_prompt("analyst_commentary").format(company=state.company_name)
    try:
        result = router.complete_json("smart", system, json.dumps(payload), _Commentary)
    except ValueError as exc:
        log.warning("analyst_commentary_failed", error=str(exc))
        return []
    valid_ids = {m.metric_id for m in analysis.metrics}
    claims: list[Claim] = []
    for item in result.claims:
        metric_ids = [m for m in item.metric_ids if m in valid_ids]
        if not metric_ids:
            log.warning("commentary_claim_without_metric_dropped", text=item.text[:80])
            continue
        claims.append(
            Claim(text=item.text, metric_ids=metric_ids, section="financial_health")
        )
    return claims


def analyst_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: compute financial analysis from EDGAR XBRL data."""
    try:
        facts = state.facts or fetch_financial_facts(state.cik)
    except Exception as exc:
        log.warning("xbrl_unavailable", error=str(exc))
        return {
            "analysis": FinancialAnalysis(),
            "data_gaps": [f"XBRL financial data unavailable: {exc}"],
        }

    analysis = compute_metrics(facts)
    if not analysis.metrics:
        return {
            "facts": facts,
            "analysis": analysis,
            "data_gaps": ["No computable financial metrics found in XBRL data."],
        }

    router = LLMRouter(state.run_id)
    try:
        analysis.commentary = _write_commentary(router, state, analysis)
    except Exception as exc:
        log.warning("analyst_commentary_skipped", error=str(exc))

    log.info(
        "analysis_complete",
        metrics=len(analysis.metrics),
        anomalies=len(analysis.anomalies),
        commentary=len(analysis.commentary),
    )
    return {"facts": facts, "analysis": analysis}
