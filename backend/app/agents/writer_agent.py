"""Writer agent: produces the structured DDReport (smart model).

First pass writes the full report via structured output. Revision passes
rewrite only the claims the critic rejected, using their cited chunks.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import settings
from app.llm.router import LLMRouter, load_prompt
from app.logging_setup import get_logger
from app.report.schema import (
    CompanyMeta,
    DDReport,
    EarningsQualitySection,
    FinancialHealthSection,
    InsiderActivitySection,
    InvestmentScorecardSection,
    MetricsTable,
    ReportMetadata,
    RiskEntry,
    ValuationSection,
)
from app.state import AgentState, Claim, RetrievedEvidence

log = get_logger(__name__)

# Token budget constants
# Writer call budget: 16 chunks×500 chars ≈ 2k input + 400 prompt ≈ 2.5k input.
# Output capped at 2500 so total ≤ 5k tokens — fits in Groq 70b 6k TPM window
# with headroom for analyst_commentary + management_questions (same window).
_MAX_EVIDENCE_CHARS = 500
_MAX_EVIDENCE_CHUNKS = 12
_WRITER_MAX_TOKENS = 2500
_MARKET_CHUNK_PREFIX = "mkt_"


# ── Synthetic evidence chunks (filing evidence + market/news data) ────────────

def _evidence_block(evidence: list[RetrievedEvidence]) -> str:
    """Render the top evidence chunks as a text block for the LLM prompt."""
    best = sorted(evidence, key=lambda e: e.score, reverse=True)[:_MAX_EVIDENCE_CHUNKS]
    lines = [
        f"chunk_id={e.chunk_id} [{e.form_type} {e.fiscal_period} — {e.section}]\n"
        f"{e.text[:_MAX_EVIDENCE_CHARS]}"
        for e in best
    ]
    return "\n\n---\n\n".join(lines) if lines else "(no filing evidence retrieved)"


def _synthetic_market_evidence(state: AgentState) -> list[RetrievedEvidence]:
    """Build synthetic RetrievedEvidence chunks from market snapshot + news.

    These give the writer something to cite — and the critic something to
    verify against — even when no SEC filing chunks were retrieved.
    """
    chunks: list[RetrievedEvidence] = []
    if state.market and state.market.available:
        m = state.market
        lines = [f"Market data for {m.ticker} (source: Yahoo Finance):"]
        if m.price:
            lines.append(f"  Price: ${m.price:,.2f}")
        if m.change_1y_pct is not None:
            lines.append(f"  1-year price change: {m.change_1y_pct:+.2f}%")
        if m.high_52w:
            lines.append(f"  52-week high: ${m.high_52w:,.2f}")
        if m.low_52w:
            lines.append(f"  52-week low: ${m.low_52w:,.2f}")
        if m.market_cap:
            lines.append(f"  Market cap: ${m.market_cap/1e9:,.1f}B")
        if m.pe_ttm:
            lines.append(f"  Trailing P/E: {m.pe_ttm:.1f}x")
        if m.forward_pe:
            lines.append(f"  Forward P/E: {m.forward_pe:.1f}x")
        if m.ev_to_ebitda:
            lines.append(f"  EV/EBITDA: {m.ev_to_ebitda:.1f}x")
        if m.price_to_sales:
            lines.append(f"  Price/Sales: {m.price_to_sales:.1f}x")
        if m.price_to_book:
            lines.append(f"  Price/Book: {m.price_to_book:.1f}x")
        if m.beta:
            lines.append(f"  Beta: {m.beta:.2f}")
        if m.recommendation:
            lines.append(f"  Analyst recommendation: {m.recommendation}")
        if m.target_mean and m.num_analysts:
            lines.append(
                f"  Analyst price target: mean ${m.target_mean:.2f} "
                f"(high ${m.target_high:.2f}, low ${m.target_low:.2f}) "
                f"from {m.num_analysts} analysts"
            )
        if m.short_percent_float:
            lines.append(f"  Short interest: {m.short_percent_float*100:.1f}% of float")
        if m.dividend_yield:
            lines.append(f"  Dividend yield: {m.dividend_yield*100:.2f}%")
        if m.sector:
            lines.append(f"  Sector: {m.sector} / {m.industry}")
        if m.peers:
            peer_strs = []
            for p in m.peers:
                parts = [p.ticker]
                if p.pe_ttm:
                    parts.append(f"P/E {p.pe_ttm:.1f}x")
                if p.ev_to_ebitda:
                    parts.append(f"EV/EBITDA {p.ev_to_ebitda:.1f}x")
                peer_strs.append(" ".join(parts))
            lines.append("  Peer comparables: " + "; ".join(peer_strs))
        if m.summary:
            lines.append(f"  Market summary: {m.summary}")
        if m.macro_notes:
            lines.append("  Macro context: " + "; ".join(m.macro_notes))
        chunks.append(RetrievedEvidence(
            chunk_id=f"{_MARKET_CHUNK_PREFIX}snapshot",
            text="\n".join(lines),
            source_url=f"https://finance.yahoo.com/quote/{m.ticker}",
            form_type="market",
            fiscal_period="current",
            section="Market Snapshot",
            score=0.9,
        ))

    if state.news and state.news.available and state.news.items:
        news_lines = ["Recent news headlines (source: Google News RSS):"]
        for item in state.news.items[:12]:
            pub = f" ({item.published_at.strftime('%Y-%m-%d')})" if item.published_at else ""
            snip = f" — {item.snippet[:200]}" if item.snippet else ""
            news_lines.append(f"  [{item.sentiment.upper()}] {item.title}{pub}{snip}")
        chunks.append(RetrievedEvidence(
            chunk_id=f"{_MARKET_CHUNK_PREFIX}news",
            text="\n".join(news_lines),
            source_url="https://news.google.com",
            form_type="news",
            fiscal_period="current",
            section="Recent News",
            score=0.8,
        ))

    if state.insider_activity and state.insider_activity.available:
        ia = state.insider_activity
        insider_lines = [
            f"Insider activity for {state.ticker} (source: SEC Form 4 via Yahoo Finance):",
            f"  Net sentiment: {ia.sentiment.upper()}",
            f"  Net shares (buys - sells): {ia.net_shares:,.0f}",
        ]
        if ia.net_value:
            insider_lines.append(f"  Net value: ${abs(ia.net_value)/1e6:,.1f}M")
        for txn in ia.transactions[:6]:
            insider_lines.append(
                f"  {txn.transaction_type}: {txn.name} ({txn.title}) — "
                f"{txn.shares:,.0f} shares"
                + (f" @ ${txn.value/txn.shares:,.2f}" if txn.value and txn.shares > 0 else "")
                + f" on {txn.date}"
            )
        chunks.append(RetrievedEvidence(
            chunk_id=f"{_MARKET_CHUNK_PREFIX}insiders",
            text="\n".join(insider_lines),
            source_url=f"https://finance.yahoo.com/quote/{state.ticker}/insider-transactions",
            form_type="insider",
            fiscal_period="trailing12m",
            section="Insider Activity",
            score=0.85,
        ))

    return chunks


# ── LLM output schema ─────────────────────────────────────────────────────────

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
    valuation_commentary: list[_WClaim] = Field(default_factory=list)
    earnings_quality_commentary: list[_WClaim] = Field(default_factory=list)
    insider_commentary: list[_WClaim] = Field(default_factory=list)
    risk_matrix: list[_WRisk] = Field(default_factory=list)
    recent_developments: list[_WClaim] = Field(default_factory=list)
    red_flags: list[_WClaim] = Field(default_factory=list)


class _Revision(BaseModel):
    action: Literal["revise", "drop"]
    text: str = ""
    citation_chunk_ids: list[str] = Field(default_factory=list)


# ── Helper functions ──────────────────────────────────────────────────────────

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
    insider: dict[str, Any] = {"available": False}
    if state.insider_activity is not None:
        insider = state.insider_activity.model_dump(mode="json")
    scorecard: dict[str, Any] = {}
    if state.scorecard and state.scorecard.available:
        scorecard = state.scorecard.model_dump(mode="json")
    return json.dumps(
        {
            "METRICS": metrics,
            "ANOMALIES": anomalies,
            "MARKET": market,
            "NEWS": news,
            "INSIDER": insider,
            "SCORECARD": scorecard,
            "DATA_GAPS": state.data_gaps,
        },
        default=str,
    )


def _to_claims(
    items: list[_WClaim],
    section: str,
    valid_metric_ids: set[str],
    valid_chunk_ids: set[str],
) -> list[Claim]:
    claims: list[Claim] = []
    for item in items:
        metric_ids = [m for m in item.metric_ids if m in valid_metric_ids]
        citation_ids = [
            cid for cid in item.citation_chunk_ids
            if cid in valid_chunk_ids or cid.startswith(_MARKET_CHUNK_PREFIX)
        ]
        if not citation_ids and not metric_ids:
            log.warning("uncited_claim_dropped", section=section, text=item.text[:80])
            continue
        claims.append(
            Claim(
                text=item.text,
                citation_chunk_ids=citation_ids,
                metric_ids=metric_ids,
                section=section,
            )
        )
    return claims


def _build_earnings_quality(state: AgentState) -> EarningsQualitySection:
    """Populate the earnings quality section from deterministic metrics."""
    section = EarningsQualitySection()
    if state.analysis is None:
        return section
    m = {m.metric_id: m.value for m in state.analysis.metrics}
    section.accruals_ratio = m.get("accruals_ratio")
    section.cash_conversion = m.get("cash_conversion")
    if section.accruals_ratio is not None:
        if section.accruals_ratio < 2:
            section.quality_label = "High"
        elif section.accruals_ratio < 8:
            section.quality_label = "Medium"
        else:
            section.quality_label = "Low"
            section.flags.append(
                f"High accruals ratio ({section.accruals_ratio:.1f}%) indicates earnings "
                "may not be fully backed by cash flows."
            )
    if section.cash_conversion is not None and section.cash_conversion < 0.8:
        section.flags.append(
            f"Cash conversion of {section.cash_conversion:.2f}x — operating cash flow "
            "significantly below reported net income."
        )
    return section


def _build_valuation_section(state: AgentState) -> ValuationSection:
    """Populate the valuation section from the market snapshot."""
    if state.market is None or not state.market.available:
        return ValuationSection()
    m = state.market
    return ValuationSection(
        pe_ttm=m.pe_ttm,
        forward_pe=m.forward_pe,
        ev_to_ebitda=m.ev_to_ebitda,
        price_to_sales=m.price_to_sales,
        price_to_book=m.price_to_book,
        beta=m.beta,
        target_mean=m.target_mean,
        target_high=m.target_high,
        target_low=m.target_low,
        recommendation=m.recommendation,
        recommendation_mean=m.recommendation_mean,
        num_analysts=m.num_analysts,
        short_percent_float=m.short_percent_float,
        short_ratio=m.short_ratio,
        dividend_yield=m.dividend_yield,
        payout_ratio=m.payout_ratio,
        peers=m.peers,
    )


def _build_insider_section(state: AgentState) -> InsiderActivitySection:
    """Populate the insider activity section from the fetched data."""
    if state.insider_activity is None or not state.insider_activity.available:
        return InsiderActivitySection()
    ia = state.insider_activity
    return InsiderActivitySection(
        available=True,
        sentiment=ia.sentiment,
        net_shares=ia.net_shares,
        net_value=ia.net_value,
        transactions=ia.transactions[:10],
    )


def _build_scorecard_section(state: AgentState) -> InvestmentScorecardSection:
    """Populate the scorecard section."""
    if state.scorecard is None or not state.scorecard.available:
        return InvestmentScorecardSection()
    sc = state.scorecard
    return InvestmentScorecardSection(
        available=True,
        dimensions=[d.model_dump() for d in sc.dimensions],
        composite_score=sc.composite_score,
        composite_label=sc.composite_label,
    )


# ── Core write & revise ───────────────────────────────────────────────────────

def _full_write(router: LLMRouter, state: AgentState) -> DDReport:
    # Supplement filing evidence with market/news/insider synthetic chunks.
    synthetic = _synthetic_market_evidence(state)
    all_evidence = list(state.evidence) + synthetic

    system = load_prompt("writer").format(
        company=state.company_name, ticker=state.ticker, focus=state.focus or "general"
    )
    user = (
        f"EVIDENCE:\n{_evidence_block(all_evidence)}\n\n"
        f"STRUCTURED DATA:\n{_context_payload(state)}"
    )
    output = router.complete_json(
        "writer", system, user, _WriterOutput, max_tokens=_WRITER_MAX_TOKENS
    )

    valid_metric_ids = (
        {m.metric_id for m in state.analysis.metrics} if state.analysis else set()
    )
    valid_chunk_ids = {e.chunk_id for e in all_evidence}

    commentary = _to_claims(output.financial_commentary, "financial_health",
                            valid_metric_ids, valid_chunk_ids)
    if state.analysis and state.analysis.commentary:
        commentary.extend(state.analysis.commentary)

    # Build the report with all new sections.
    valuation_section = _build_valuation_section(state)
    valuation_section.commentary = _to_claims(
        output.valuation_commentary, "valuation", valid_metric_ids, valid_chunk_ids
    )

    earnings_quality_section = _build_earnings_quality(state)
    earnings_quality_section.commentary = _to_claims(
        output.earnings_quality_commentary, "earnings_quality", valid_metric_ids, valid_chunk_ids
    )

    insider_section = _build_insider_section(state)
    insider_section.commentary = _to_claims(
        output.insider_commentary, "insider_activity", valid_metric_ids, valid_chunk_ids
    )

    snapshot = state.market
    return DDReport(
        company=CompanyMeta(
            name=state.company_name,
            ticker=state.ticker,
            cik=state.cik,
            sector=snapshot.sector if snapshot and snapshot.available else "",
            industry=snapshot.industry if snapshot and snapshot.available else "",
        ),
        executive_summary=_to_claims(output.executive_summary, "executive_summary",
                                     valid_metric_ids, valid_chunk_ids),
        business_overview=_to_claims(output.business_overview, "business_overview",
                                     valid_metric_ids, valid_chunk_ids),
        financial_health=FinancialHealthSection(
            table=MetricsTable(metrics=state.analysis.metrics if state.analysis else []),
            commentary=commentary,
        ),
        valuation=valuation_section,
        earnings_quality=earnings_quality_section,
        insider_activity=insider_section,
        scorecard=_build_scorecard_section(state),
        risk_matrix=[
            RiskEntry(
                title=risk.title,
                severity=risk.severity,
                likelihood=risk.likelihood,
                claims=_to_claims(risk.claims, "risk_matrix", valid_metric_ids, valid_chunk_ids),
            )
            for risk in output.risk_matrix
        ],
        recent_developments=_to_claims(output.recent_developments, "recent_developments",
                                       valid_metric_ids, valid_chunk_ids),
        red_flags=_to_claims(output.red_flags, "red_flags", valid_metric_ids, valid_chunk_ids),
        management_questions=list(state.management_questions),
        data_gaps=list(state.data_gaps),
        metadata=ReportMetadata(run_id=state.run_id),
    )


def _revise_claims(router: LLMRouter, state: AgentState) -> DDReport:
    assert state.report is not None
    report = state.report
    evidence_by_id = {e.chunk_id: e for e in state.evidence}
    # Also include synthetic evidence in revision context.
    for chunk in _synthetic_market_evidence(state):
        evidence_by_id[chunk.chunk_id] = chunk

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
                    for e in list(state.evidence)[:10]
                ],
            }
        )
        try:
            revision = router.complete_json(
                "writer", load_prompt("writer_revision"), user, _Revision, max_tokens=500
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


def _partial_report(state: AgentState, reason: str) -> DDReport:
    """Metrics-only report when the writer LLM is unavailable."""
    snapshot = state.market
    return DDReport(
        company=CompanyMeta(
            name=state.company_name or state.company_input,
            ticker=state.ticker,
            cik=state.cik,
            sector=snapshot.sector if snapshot and snapshot.available else "",
            industry=snapshot.industry if snapshot and snapshot.available else "",
        ),
        financial_health=FinancialHealthSection(
            table=MetricsTable(metrics=state.analysis.metrics if state.analysis else []),
            commentary=list(state.analysis.commentary) if state.analysis else [],
        ),
        valuation=_build_valuation_section(state),
        earnings_quality=_build_earnings_quality(state),
        insider_activity=_build_insider_section(state),
        scorecard=_build_scorecard_section(state),
        management_questions=list(state.management_questions),
        data_gaps=[*state.data_gaps, f"Report prose unavailable (writer LLM failed): {reason}"],
        metadata=ReportMetadata(run_id=state.run_id),
    )


def writer_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: write the report, or revise critic-rejected claims."""
    router = LLMRouter(state.run_id)
    if state.report is None:
        try:
            report = _full_write(router, state)
        except Exception as exc:
            log.error("writer_failed_shipping_partial", error=str(exc)[:300])
            return {
                "report": _partial_report(state, str(exc)[:200]),
                "status": "written_partial",
                "data_gaps": [f"Report prose unavailable (writer LLM failed): {exc}"[:300]],
            }
        log.info("report_written", claims=len(report.all_claims()))
        return {"report": report, "status": "written"}

    report = _revise_claims(router, state)
    revision = state.revision_count + 1
    log.info("report_revised", revision=revision,
             max_revisions=settings.critic_max_revisions)
    return {"report": report, "revision_count": revision, "status": "revised"}

log = get_logger(__name__)

# These are the canonical constants — defined at top of file (lines 37-39).
# Kept here as a comment only so callers remain readable.
_MARKET_CHUNK_PREFIX = "mkt_"  # synthetic chunk_ids for market/news evidence


def _synthetic_market_evidence(state: AgentState) -> list[RetrievedEvidence]:
    """Build synthetic RetrievedEvidence chunks from market snapshot + news.

    These give the writer something to cite — and the critic something to
    verify against — even when no SEC filing chunks were retrieved.
    """
    chunks: list[RetrievedEvidence] = []
    if state.market and state.market.available:
        m = state.market
        lines = [f"Market data for {m.ticker} (source: Yahoo Finance):"]
        if m.price:
            lines.append(f"  Price: ${m.price:,.2f}")
        if m.change_1y_pct is not None:
            lines.append(f"  1-year price change: {m.change_1y_pct:+.2f}%")
        if m.high_52w:
            lines.append(f"  52-week high: ${m.high_52w:,.2f}")
        if m.low_52w:
            lines.append(f"  52-week low: ${m.low_52w:,.2f}")
        if m.market_cap:
            lines.append(f"  Market cap: ${m.market_cap/1e9:,.1f}B")
        if m.pe_ttm:
            lines.append(f"  Trailing P/E: {m.pe_ttm:.1f}x")
        if m.forward_pe:
            lines.append(f"  Forward P/E: {m.forward_pe:.1f}x")
        if m.ev_to_ebitda:
            lines.append(f"  EV/EBITDA: {m.ev_to_ebitda:.1f}x")
        if m.price_to_sales:
            lines.append(f"  Price/Sales: {m.price_to_sales:.1f}x")
        if m.peers:
            peer_strs = []
            for p in m.peers:
                parts = [p.ticker]
                if p.pe_ttm:
                    parts.append(f"P/E {p.pe_ttm:.1f}x")
                if p.ev_to_ebitda:
                    parts.append(f"EV/EBITDA {p.ev_to_ebitda:.1f}x")
                peer_strs.append(" ".join(parts))
            lines.append("  Peer comparables: " + "; ".join(peer_strs))
        if m.summary:
            lines.append(f"  Analyst summary: {m.summary}")
        if m.macro_notes:
            lines.append("  Macro context: " + "; ".join(m.macro_notes))
        text = "\n".join(lines)
        chunks.append(RetrievedEvidence(
            chunk_id=f"{_MARKET_CHUNK_PREFIX}snapshot",
            text=text,
            source_url=f"https://finance.yahoo.com/quote/{m.ticker}",
            form_type="market",
            fiscal_period="current",
            section="Market Snapshot",
            score=0.9,
        ))
    if state.news and state.news.available and state.news.items:
        news_lines = ["Recent news headlines (source: Google News RSS):"]
        for item in state.news.items[:12]:
            pub = f" ({item.published_at.strftime('%Y-%m-%d')})" if item.published_at else ""
            snip = f" — {item.snippet[:200]}" if item.snippet else ""
            news_lines.append(f"  [{item.sentiment.upper()}] {item.title}{pub}{snip}")
        chunks.append(RetrievedEvidence(
            chunk_id=f"{_MARKET_CHUNK_PREFIX}news",
            text="\n".join(news_lines),
            source_url="https://news.google.com",
            form_type="news",
            fiscal_period="current",
            section="Recent News",
            score=0.8,
        ))
    return chunks


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


_MARKET_CHUNK_PREFIX = "mkt_"  # synthetic chunk_ids for market/news evidence


def _synthetic_market_evidence(state: AgentState) -> list[RetrievedEvidence]:
    """Build synthetic RetrievedEvidence from market snapshot + news so the
    writer can cite them and the critic can verify against them.
    """
    chunks: list[RetrievedEvidence] = []
    if state.market and state.market.available:
        m = state.market
        lines = [
            f"Market data for {m.ticker} (source: Yahoo Finance / yfinance):",
            f"  Price: ${m.price}" if m.price else "",
            f"  1Y change: {m.change_1y_pct}%" if m.change_1y_pct is not None else "",
            f"  52W high: ${m.high_52w}" if m.high_52w else "",
            f"  52W low: ${m.low_52w}" if m.low_52w else "",
            f"  Market cap: ${m.market_cap:,.0f}" if m.market_cap else "",
            f"  P/E TTM: {m.pe_ttm}" if m.pe_ttm else "",
            f"  Forward P/E: {m.forward_pe}" if m.forward_pe else "",
            f"  EV/EBITDA: {m.ev_to_ebitda}" if m.ev_to_ebitda else "",
        ]
        if m.peers:
            lines.append("  Peers: " + ", ".join(
                f"{p.ticker} (P/E {p.pe_ttm}, EV/EBITDA {p.ev_to_ebitda})"
                for p in m.peers if p.pe_ttm or p.ev_to_ebitda
            ))
        if m.summary:
            lines.append(f"  Summary: {m.summary}")
        text = "\n".join(l for l in lines if l)
        if text:
            chunks.append(RetrievedEvidence(
                chunk_id=f"{_MARKET_CHUNK_PREFIX}snapshot",
                text=text,
                source_url=f"https://finance.yahoo.com/quote/{m.ticker}",
                form_type="market",
                fiscal_period="current",
                section="Market Snapshot",
                score=0.9,
            ))
    if state.news and state.news.available and state.news.items:
        news_lines = ["Recent news (source: Google News RSS):"]
        for item in state.news.items[:10]:
            news_lines.append(
                f"  [{item.sentiment}] {item.title}"
                + (f" ({item.published_at.strftime('%Y-%m-%d')})"
                   if item.published_at else "")
                + (f" — {item.snippet[:200]}" if item.snippet else "")
            )
        chunks.append(RetrievedEvidence(
            chunk_id=f"{_MARKET_CHUNK_PREFIX}news",
            text="\n".join(news_lines),
            source_url="https://news.google.com",
            form_type="news",
            fiscal_period="current",
            section="Recent News",
            score=0.8,
        ))
    return chunks

    best = sorted(evidence, key=lambda e: e.score, reverse=True)[:_MAX_EVIDENCE_CHUNKS]
    lines = [
        f"chunk_id={e.chunk_id} [{e.form_type} {e.fiscal_period} — {e.section}]\n"
        f"{e.text[:_MAX_EVIDENCE_CHARS]}"
        for e in best
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


def _to_claims(
    items: list[_WClaim],
    section: str,
    valid_metric_ids: set[str],
    valid_chunk_ids: set[str],
) -> list[Claim]:
    claims: list[Claim] = []
    for item in items:
        metric_ids = [m for m in item.metric_ids if m in valid_metric_ids]
        # Accept citations to filing chunks, market/news synthetic chunks, or metrics.
        citation_ids = [
            cid for cid in item.citation_chunk_ids
            if cid in valid_chunk_ids or cid.startswith(_MARKET_CHUNK_PREFIX)
        ]
        if not citation_ids and not metric_ids:
            log.warning("uncited_claim_dropped", section=section, text=item.text[:80])
            continue
        claims.append(
            Claim(
                text=item.text,
                citation_chunk_ids=citation_ids,
                metric_ids=metric_ids,
                section=section,
            )
        )
    return claims


def _full_write(router: LLMRouter, state: AgentState) -> DDReport:
    # Always supplement filing evidence with market/news synthetic chunks.
    synthetic = _synthetic_market_evidence(state)
    all_evidence = list(state.evidence) + synthetic

    system = load_prompt("writer").format(
        company=state.company_name, ticker=state.ticker, focus=state.focus or "general"
    )
    user = (
        f"EVIDENCE:\n{_evidence_block(all_evidence)}\n\n"
        f"STRUCTURED DATA:\n{_context_payload(state)}"
    )
    output = router.complete_json(
        "writer", system, user, _WriterOutput, max_tokens=_WRITER_MAX_TOKENS
    )

    valid_metric_ids = (
        {m.metric_id for m in state.analysis.metrics} if state.analysis else set()
    )
    valid_chunk_ids = {e.chunk_id for e in all_evidence}
    commentary = _to_claims(output.financial_commentary, "financial_health",
                            valid_metric_ids, valid_chunk_ids)
    if state.analysis and state.analysis.commentary:
        commentary.extend(state.analysis.commentary)

    return DDReport(
        company=CompanyMeta(name=state.company_name, ticker=state.ticker, cik=state.cik),
        executive_summary=_to_claims(output.executive_summary, "executive_summary",
                                     valid_metric_ids, valid_chunk_ids),
        business_overview=_to_claims(output.business_overview, "business_overview",
                                     valid_metric_ids, valid_chunk_ids),
        financial_health=FinancialHealthSection(
            table=MetricsTable(metrics=state.analysis.metrics if state.analysis else []),
            commentary=commentary,
        ),
        risk_matrix=[
            RiskEntry(
                title=risk.title,
                severity=risk.severity,
                likelihood=risk.likelihood,
                claims=_to_claims(risk.claims, "risk_matrix", valid_metric_ids, valid_chunk_ids),
            )
            for risk in output.risk_matrix
        ],
        recent_developments=_to_claims(output.recent_developments, "recent_developments",
                                       valid_metric_ids, valid_chunk_ids),
        red_flags=_to_claims(output.red_flags, "red_flags", valid_metric_ids, valid_chunk_ids),
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
                "writer", load_prompt("writer_revision"), user, _Revision, max_tokens=500
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


def _partial_report(state: AgentState, reason: str) -> DDReport:
    """Metrics-only report when the writer LLM is unavailable — the run still
    ships its deterministic content rather than failing outright."""
    return DDReport(
        company=CompanyMeta(
            name=state.company_name or state.company_input,
            ticker=state.ticker,
            cik=state.cik,
        ),
        financial_health=FinancialHealthSection(
            table=MetricsTable(metrics=state.analysis.metrics if state.analysis else []),
            commentary=list(state.analysis.commentary) if state.analysis else [],
        ),
        data_gaps=[*state.data_gaps, f"Report prose unavailable (writer LLM failed): {reason}"],
        metadata=ReportMetadata(run_id=state.run_id),
    )


def writer_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: write the report, or revise critic-rejected claims."""
    router = LLMRouter(state.run_id)
    if state.report is None:
        try:
            report = _full_write(router, state)
        except Exception as exc:
            log.error("writer_failed_shipping_partial", error=str(exc)[:300])
            return {
                "report": _partial_report(state, str(exc)[:200]),
                "status": "written_partial",
                "data_gaps": [f"Report prose unavailable (writer LLM failed): {exc}"[:300]],
            }
        log.info("report_written", claims=len(report.all_claims()))
        return {"report": report, "status": "written"}

    report = _revise_claims(router, state)
    revision = state.revision_count + 1
    log.info("report_revised", revision=revision,
             max_revisions=settings.critic_max_revisions)
    return {"report": report, "revision_count": revision, "status": "revised"}
