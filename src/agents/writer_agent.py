"""Writer agent: produces the structured DDReport (smart model).

First pass writes the full report via structured output. Revision passes
rewrite only the claims the critic rejected, using their cited chunks.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.config import settings
from src.llm.router import LLMRouter, load_prompt
from src.logging_setup import get_logger
from src.report.schema import (
    CompanyMeta,
    DDReport,
    FinancialHealthSection,
    MetricsTable,
    ReportMetadata,
    RiskEntry,
)
from src.state import AgentState, Claim, RetrievedEvidence

log = get_logger(__name__)

_MAX_EVIDENCE_CHARS = 900


class _WClaim(BaseModel):
    text: str
    citation_chunk_ids: list[str] = Field(default_factory=list)
    metric_ids: list[str] = Field(default_factory=list)


class _WRisk(BaseModel):
    title: str
    severity: Literal["low", "medium", "high"]
    likelihood: Literal["low", "medium", "high"]
    claims: list[_WClaim] = Field(default_factory=list)


class _WriterOutput(BaseModel):
    executive_summary: list[_WClaim] = Field(default_factory=list)
    business_overview: list[_WClaim] = Field(default_factory=list)
    financial_commentary: list[_WClaim] = Field(default_factory=list)
    risk_matrix: list[_WRisk] = Field(default_factory=list)
    recent_developments: list[_WClaim] = Field(default_factory=list)
    red_flags: list[_WClaim] = Field(default_factory=list)


class _Revision(BaseModel):
    action: Literal["revise", "drop"]
    text: str = ""
    citation_chunk_ids: list[str] = Field(default_factory=list)


def _evidence_block(evidence: list[RetrievedEvidence]) -> str:
    lines = [
        f"chunk_id={e.chunk_id} [{e.form_type} {e.fiscal_period} — {e.section}]\n"
        f"{e.text[:_MAX_EVIDENCE_CHARS]}"
        for e in evidence
    ]
    return "\n\n---\n\n".join(lines) if lines else "(no filing evidence retrieved)"


def _context_payload(state: AgentState) -> str:
    metrics = (
        [m.model_dump() for m in state.analysis.metrics] if state.analysis else []
    )
    anomalies = state.analysis.anomalies if state.analysis else []
    market: dict[str, Any] = {"available": False}
    if state.market is not None:
        market = state.market.model_dump(mode="json")
    news: dict[str, Any] = {"available": False}
    if state.news is not None:
        news = {
            "available": state.news.available,
            "items": [
                {"title": n.title, "sentiment": n.sentiment, "published": str(n.published_at)}
                for n in state.news.items
            ],
        }
    return json.dumps(
        {
            "METRICS": metrics,
            "ANOMALIES": anomalies,
            "MARKET": market,
            "NEWS": news,
            "DATA_GAPS": state.data_gaps,
        },
        default=str,
    )


def _to_claims(items: list[_WClaim], section: str, valid_metric_ids: set[str]) -> list[Claim]:
    claims: list[Claim] = []
    for item in items:
        metric_ids = [m for m in item.metric_ids if m in valid_metric_ids]
        if not item.citation_chunk_ids and not metric_ids:
            log.warning("uncited_claim_dropped", section=section, text=item.text[:80])
            continue
        claims.append(
            Claim(
                text=item.text,
                citation_chunk_ids=item.citation_chunk_ids,
                metric_ids=metric_ids,
                section=section,
            )
        )
    return claims


def _full_write(router: LLMRouter, state: AgentState) -> DDReport:
    system = load_prompt("writer").format(
        company=state.company_name, ticker=state.ticker, focus=state.focus or "general"
    )
    user = (
        f"EVIDENCE:\n{_evidence_block(state.evidence)}\n\n"
        f"STRUCTURED DATA:\n{_context_payload(state)}"
    )
    output = router.complete_json("writer", system, user, _WriterOutput, max_tokens=8000)

    valid_metric_ids = (
        {m.metric_id for m in state.analysis.metrics} if state.analysis else set()
    )
    commentary = _to_claims(output.financial_commentary, "financial_health", valid_metric_ids)
    if state.analysis and state.analysis.commentary:
        commentary.extend(state.analysis.commentary)

    return DDReport(
        company=CompanyMeta(name=state.company_name, ticker=state.ticker, cik=state.cik),
        executive_summary=_to_claims(output.executive_summary, "executive_summary",
                                     valid_metric_ids),
        business_overview=_to_claims(output.business_overview, "business_overview",
                                     valid_metric_ids),
        financial_health=FinancialHealthSection(
            table=MetricsTable(metrics=state.analysis.metrics if state.analysis else []),
            commentary=commentary,
        ),
        risk_matrix=[
            RiskEntry(
                title=risk.title,
                severity=risk.severity,
                likelihood=risk.likelihood,
                claims=_to_claims(risk.claims, "risk_matrix", valid_metric_ids),
            )
            for risk in output.risk_matrix
        ],
        recent_developments=_to_claims(output.recent_developments, "recent_developments",
                                       valid_metric_ids),
        red_flags=_to_claims(output.red_flags, "red_flags", valid_metric_ids),
        data_gaps=list(state.data_gaps),
        metadata=ReportMetadata(run_id=state.run_id),
    )


def _revise_claims(router: LLMRouter, state: AgentState) -> DDReport:
    assert state.report is not None
    report = state.report
    evidence_by_id = {e.chunk_id: e for e in state.evidence}
    rejected = [c for c in report.all_claims() if c.verification_status == "unsupported"]
    feedback_by_claim = {v.claim_id: v.feedback for v in state.critic_verdicts}

    for claim in rejected:
        cited = [
            evidence_by_id[cid].text[:_MAX_EVIDENCE_CHARS]
            for cid in claim.citation_chunk_ids
            if cid in evidence_by_id
        ]
        user = json.dumps(
            {
                "claim": claim.text,
                "reason": feedback_by_claim.get(claim.claim_id, "not supported"),
                "cited_chunks": [
                    {"chunk_id": cid, "text": evidence_by_id[cid].text[:_MAX_EVIDENCE_CHARS]}
                    for cid in claim.citation_chunk_ids
                    if cid in evidence_by_id
                ],
                "other_available_chunks": [
                    {"chunk_id": e.chunk_id, "text": e.text[:400]}
                    for e in state.evidence[:10]
                ],
            }
        )
        try:
            revision = router.complete_json(
                "writer", load_prompt("writer_revision"), user, _Revision
            )
        except ValueError as exc:
            log.warning("revision_failed_dropping_claim", claim_id=claim.claim_id,
                        error=str(exc))
            revision = _Revision(action="drop")

        if revision.action == "drop" or not revision.text.strip():
            report.drop_claim(claim.claim_id)
            report.data_gaps.append(
                f"Claim dropped during revision (unsupported): {claim.text[:120]}"
            )
            log.warning("claim_dropped_in_revision", claim_id=claim.claim_id,
                        text=claim.text[:120], cited=len(cited))
        else:
            claim.text = revision.text.strip()
            if revision.citation_chunk_ids:
                claim.citation_chunk_ids = [
                    cid for cid in revision.citation_chunk_ids if cid in evidence_by_id
                ]
            claim.verification_status = "unverified"
    return report


def writer_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: write the report, or revise critic-rejected claims."""
    router = LLMRouter(state.run_id)
    if state.report is None:
        report = _full_write(router, state)
        log.info("report_written", claims=len(report.all_claims()))
        return {"report": report, "status": "written"}

    report = _revise_claims(router, state)
    revision = state.revision_count + 1
    log.info("report_revised", revision=revision,
             max_revisions=settings.critic_max_revisions)
    return {"report": report, "revision_count": revision, "status": "revised"}
