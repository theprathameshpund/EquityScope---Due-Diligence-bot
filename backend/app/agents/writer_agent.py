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
    DCFAnalysisSection,
    DDReport,
    EarningsQualitySection,
    FinancialHealthSection,
    InsiderActivitySection,
    InstitutionalExecutiveSummary,
    InvestmentScorecardSection,
    InvestmentThesisSection,
    MetricsTable,
    QualitativeAnalysisSection,
    ReportQualityChecks,
    ReportMetadata,
    RiskEntry,
    ValuationSection,
)
from app.state import AgentState, Claim, MetricValue, RetrievedEvidence

log = get_logger(__name__)

# Token budget constants
# Writer uses the FAST model (8b-instant). Actual Groq on_demand TPM = 6000.
# Keep total request (input + output) under 5500 tokens to leave headroom.
# ~8 chunks × 300 chars ≈ 600 tokens input + 800 prompt ≈ 1400 input → 4000 output budget.
# Targeting input ~1400 + output 2500 = ~3900 total — safely under 6000.
_MAX_EVIDENCE_CHARS = 300
_MAX_EVIDENCE_CHUNKS = 8
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
    """Compact structured data payload — strips verbose fields that bloat token count.

    MetricValue objects carry inputs/formula dicts that are only needed for
    the XBRL table display, not for LLM prose generation.  Market/news/insider
    details are already embedded in the evidence block so we only send summary
    scalars here to avoid duplication.  Target: ≤900 tokens total.
    """
    # Compact metrics — id, value, unit, period only (drops inputs/formula/name)
    metrics_compact = [
        {"id": m.metric_id, "v": m.value, "u": m.unit, "p": m.period}
        for m in (state.analysis.metrics if state.analysis else [])
    ]
    anomalies = state.analysis.anomalies if state.analysis else []

    # Compact market — key valuation fields; full detail already in mkt_snapshot chunk
    market: dict[str, Any] = {"available": False}
    if state.market is not None and state.market.available:
        mk = state.market
        market = {
            "available": True,
            "price": mk.price,
            "mktcap_b": round(mk.market_cap / 1e9, 1) if mk.market_cap else None,
            "pe_ttm": mk.pe_ttm,
            "fwd_pe": mk.forward_pe,
            "ev_ebitda": mk.ev_to_ebitda,
            "p_s": mk.price_to_sales,
            "p_b": mk.price_to_book,
            "beta": mk.beta,
            "target_mean": mk.target_mean,
            "rec": mk.recommendation,
            "analysts": mk.num_analysts,
            "div_yield": mk.dividend_yield,
            "change_1y_pct": mk.change_1y_pct,
            "sector": mk.sector,
            "industry": mk.industry,
        }

    # Compact news — headlines only; full text already in mkt_news chunk
    news_lines: list[str] = []
    if state.news is not None and state.news.available:
        news_lines = [
            f"[{n.sentiment}] {n.title}"
            for n in state.news.items[:8]
        ]

    # Compact insider — summary scalars only; transactions in mkt_insiders chunk
    insider: dict[str, Any] = {"available": False}
    if state.insider_activity is not None and state.insider_activity.available:
        ia = state.insider_activity
        insider = {
            "available": True,
            "sentiment": ia.sentiment,
            "net_shares": ia.net_shares,
            "net_value_m": round(ia.net_value / 1e6, 1) if ia.net_value else None,
        }

    scorecard: dict[str, Any] = {}
    if state.scorecard and state.scorecard.available:
        sc = state.scorecard
        scorecard = {"score": sc.composite_score, "label": sc.composite_label}

    return json.dumps(
        {
            "METRICS": metrics_compact,
            "ANOMALIES": anomalies,
            "MARKET": market,
            "NEWS": news_lines,
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


def _find_metric(metrics: list[MetricValue], metric_id: str) -> MetricValue | None:
    for metric in metrics:
        if metric.metric_id == metric_id:
            return metric
    return None


def _find_latest_metric_with_prefix(
    metrics: list[MetricValue], metric_prefix: str
) -> MetricValue | None:
    matches = [metric for metric in metrics if metric.metric_id.startswith(metric_prefix)]
    return matches[-1] if matches else None


def _backfill_missing_sections(report: DDReport, state: AgentState) -> None:
    """Fill sparse sections with deterministic, cited claims.

    This keeps reports useful when the writer model returns too few claims for
    non-financial sections. All added claims cite metric_ids or synthetic
    market/news chunk_ids so critic verification remains grounded.
    """
    metrics = state.analysis.metrics if state.analysis else []
    market = state.market

    if not report.executive_summary:
        rev = _find_metric(metrics, "revenue_growth_yoy")
        fcf = _find_metric(metrics, "fcf")
        if rev is not None:
            report.executive_summary.append(
                Claim(
                    text=(
                        f"Revenue growth was {rev.value:.2f}% in {rev.period}, "
                        "indicating continued top-line expansion."
                    ),
                    metric_ids=[rev.metric_id],
                    section="executive_summary",
                )
            )
        if fcf is not None:
            report.executive_summary.append(
                Claim(
                    text=(
                        f"Free cash flow was {fcf.value:,.0f} {fcf.unit} in {fcf.period}, "
                        "supporting financial flexibility."
                    ),
                    metric_ids=[fcf.metric_id],
                    section="executive_summary",
                )
            )
    if not report.business_overview:
        revenue = _find_metric(metrics, "revenue")
        gross_margin = _find_latest_metric_with_prefix(metrics, "gross_margin_fy")
        if revenue is not None:
            report.business_overview.append(
                Claim(
                    text=(
                        f"Annual revenue was {revenue.value:,.0f} {revenue.unit} in {revenue.period}, "
                        "indicating the current operating scale of the business."
                    ),
                    metric_ids=[revenue.metric_id],
                    section="business_overview",
                )
            )
        if gross_margin is not None:
            report.business_overview.append(
                Claim(
                    text=(
                        f"Gross margin was {gross_margin.value:.2f}% in {gross_margin.period}, "
                        "reflecting product mix and pricing power."
                    ),
                    metric_ids=[gross_margin.metric_id],
                    section="business_overview",
                )
            )

    if not report.recent_developments:
        net_margin_trend = _find_metric(metrics, "net_margin_trend_bps")
        share_dilution = _find_metric(metrics, "share_dilution")
        if net_margin_trend is not None:
            report.recent_developments.append(
                Claim(
                    text=(
                        f"Net margin changed by {net_margin_trend.value:.1f} bps in {net_margin_trend.period}, "
                        "showing a recent shift in profitability."
                    ),
                    metric_ids=[net_margin_trend.metric_id],
                    section="recent_developments",
                )
            )
        if share_dilution is not None:
            report.recent_developments.append(
                Claim(
                    text=(
                        f"Share dilution rate was {share_dilution.value:.2f}% over {share_dilution.period}."
                    ),
                    metric_ids=[share_dilution.metric_id],
                    section="recent_developments",
                )
            )

    if not report.risk_matrix:
        risks: list[RiskEntry] = []
        current_ratio = _find_metric(metrics, "current_ratio")
        debt_ratio = _find_metric(metrics, "debt_to_ebitda")
        rev_growth = _find_metric(metrics, "revenue_growth_yoy")

        if current_ratio is not None and current_ratio.value < 1.0:
            risks.append(
                RiskEntry(
                    title="Liquidity Cushion",
                    severity="medium",
                    likelihood="medium",
                    claims=[
                        Claim(
                            text=(
                                f"Current ratio is {current_ratio.value:.2f}x in {current_ratio.period}, "
                                "which is below 1.0x and may limit short-term liquidity flexibility."
                            ),
                            metric_ids=[current_ratio.metric_id],
                            section="risk_matrix",
                        )
                    ],
                )
            )

        if debt_ratio is not None and debt_ratio.value >= 2.0:
            risks.append(
                RiskEntry(
                    title="Leverage Pressure",
                    severity="medium",
                    likelihood="low",
                    claims=[
                        Claim(
                            text=(
                                f"Debt to EBITDA is {debt_ratio.value:.2f}x in {debt_ratio.period}, "
                                "which can increase financing and refinancing risk."
                            ),
                            metric_ids=[debt_ratio.metric_id],
                            section="risk_matrix",
                        )
                    ],
                )
            )

        if rev_growth is not None and rev_growth.value < 3.0:
            risks.append(
                RiskEntry(
                    title="Low Growth Momentum",
                    severity="medium",
                    likelihood="medium",
                    claims=[
                        Claim(
                            text=(
                                f"Revenue growth was {rev_growth.value:.2f}% in {rev_growth.period}, "
                                "which indicates modest top-line momentum."
                            ),
                            metric_ids=[rev_growth.metric_id],
                            section="risk_matrix",
                        )
                    ],
                )
            )

        if market and market.available and market.beta is not None and market.beta > 1.2:
            if rev_growth is not None:
                risks.append(
                    RiskEntry(
                        title="Growth Sensitivity",
                        severity="low",
                        likelihood="medium",
                        claims=[
                            Claim(
                                text=(
                                    "Higher trading volatility can amplify downside during periods of slower growth."
                                ),
                                metric_ids=[rev_growth.metric_id],
                                section="risk_matrix",
                            )
                        ],
                    )
                )

        report.risk_matrix = risks[:3]

    if not report.red_flags:
        current_ratio = _find_metric(metrics, "current_ratio")
        accruals = _find_metric(metrics, "accruals_ratio")
        if current_ratio is not None and current_ratio.value < 1.0:
            report.red_flags.append(
                Claim(
                    text=(
                        f"Current ratio is {current_ratio.value:.2f}x in {current_ratio.period}, below 1.0x."
                    ),
                    metric_ids=[current_ratio.metric_id],
                    section="red_flags",
                )
            )
        if accruals is not None and accruals.value >= 8.0:
            report.red_flags.append(
                Claim(
                    text=(
                        f"Accruals ratio is {accruals.value:.2f}% in {accruals.period}, which may indicate lower earnings quality."
                    ),
                    metric_ids=[accruals.metric_id],
                    section="red_flags",
                )
            )


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


def _metric_value(state: AgentState, metric_id: str) -> float | None:
    if state.analysis is None:
        return None
    metric = state.analysis.metric_by_id(metric_id)
    return metric.value if metric else None


def _overall_score_100(state: AgentState, report: DDReport | None = None) -> int:
    if state.scorecard and state.scorecard.available:
        base = state.scorecard.composite_score / 5.0 * 100
    else:
        base = 50.0
    risk_penalty = 0.0
    if report is not None:
        risk_penalty += 8.0 * sum(1 for risk in report.risk_matrix if risk.severity == "high")
        risk_penalty += 4.0 * len(report.red_flags)
    return max(0, min(100, round(base - risk_penalty)))


def _rating_from_score(score: int) -> str:
    if score >= 82:
        return "Strong Buy"
    if score >= 65:
        return "Buy"
    if score >= 45:
        return "Hold"
    if score >= 25:
        return "Sell"
    return "Strong Sell"


def _expected_return_range(state: AgentState) -> str:
    market = state.market
    if market and market.available and market.price and market.target_low and market.target_high:
        low = (market.target_low / market.price - 1.0) * 100.0
        high = (market.target_high / market.price - 1.0) * 100.0
        return f"{low:+.0f}% to {high:+.0f}% based on Yahoo Finance analyst target range."
    return "Data unavailable or unverifiable."


def _build_institutional_summary(state: AgentState, report: DDReport) -> InstitutionalExecutiveSummary:
    score = _overall_score_100(state, report)
    market = state.market
    confidence = 35
    confidence += 20 if state.analysis and state.analysis.metrics else 0
    confidence += 15 if state.evidence else 0
    confidence += 10 if market and market.available else 0
    confidence += 10 if state.news and state.news.available and state.news.items else 0
    confidence += 10 if state.insider_activity and state.insider_activity.available else 0
    confidence = min(confidence, 100)

    bull: list[str] = []
    bear: list[str] = []
    catalysts: list[str] = []
    risks: list[str] = []

    rev_growth = _metric_value(state, "revenue_growth_yoy")
    fcf_margin = _metric_value(state, "fcf_margin")
    roic = _metric_value(state, "roic")
    debt = _metric_value(state, "debt_to_ebitda")
    current = _metric_value(state, "current_ratio")

    if rev_growth is not None:
        (bull if rev_growth > 0 else bear).append(f"Revenue growth was {rev_growth:.2f}% in the latest fiscal year.")
    if fcf_margin is not None:
        (bull if fcf_margin > 5 else bear).append(f"Free-cash-flow margin was {fcf_margin:.2f}%.")
    if roic is not None:
        (bull if roic > 10 else bear).append(f"ROIC was {roic:.2f}%.")
    if debt is not None:
        (bull if debt <= 2.5 else bear).append(f"Debt/EBITDA was {debt:.2f}x.")
    if current is not None:
        (bull if current >= 1.0 else bear).append(f"Current ratio was {current:.2f}x.")

    for risk in report.risk_matrix[:3]:
        risks.append(risk.title)
    if market and market.available:
        if market.target_mean and market.price:
            catalysts.append("Analyst target revisions and estimate changes.")
        if market.change_1y_pct is not None:
            catalysts.append("Sustained share-price momentum or reversal versus the last 12 months.")
    if state.news and state.news.items:
        catalysts.append("Recent company-specific news flow from Google News RSS.")

    while len(bull) < 5:
        bull.append("Data unavailable or unverifiable.")
    while len(bear) < 5:
        bear.append("Data unavailable or unverifiable.")
    while len(catalysts) < 3:
        catalysts.append("Data unavailable or unverifiable.")
    while len(risks) < 3:
        risks.append("Data unavailable or unverifiable.")

    return InstitutionalExecutiveSummary(
        investment_rating=_rating_from_score(score),
        confidence_score=confidence,
        investment_horizon="3 Year",
        key_bull_thesis=bull[:5],
        key_bear_thesis=bear[:5],
        top_catalysts=catalysts[:3],
        top_risks=risks[:3],
        expected_return_range=_expected_return_range(state),
    )


def _score10(value: float | None, neutral: float = 5.0) -> float:
    return round(max(0.0, min(10.0, value if value is not None else neutral)), 1)


def _build_business_quality(state: AgentState) -> QualitativeAnalysisSection:
    growth = _metric_value(state, "revenue_cagr_3y") or _metric_value(state, "revenue_growth_yoy")
    margin = _metric_value(state, "gross_margin_trend_bps")
    score = 5.0
    if growth is not None:
        score += 2 if growth > 10 else 1 if growth > 0 else -1
    if margin is not None:
        score += 1 if margin > 0 else -1 if margin < -300 else 0
    summary = []
    if state.market and state.market.available:
        summary.append(f"Industry classification from Yahoo Finance: {state.market.sector or 'Data unavailable or unverifiable.'} / {state.market.industry or 'Data unavailable or unverifiable.'}.")
    if growth is not None:
        summary.append(f"Growth evidence: revenue growth metric is {growth:.2f}%.")
    unavailable = [
        "Segment revenue breakdown.",
        "Geographic revenue breakdown.",
        "Customer concentration.",
        "Supplier concentration.",
        "Market share trends.",
        "Patents and technology-leadership proof.",
    ]
    return QualitativeAnalysisSection(score=_score10(score), summary=summary, data_unavailable=unavailable)


def _build_management_analysis(state: AgentState) -> QualitativeAnalysisSection:
    score = 5.0
    dilution = _metric_value(state, "share_dilution")
    if dilution is not None:
        score += 1 if dilution <= 0 else -1 if dilution > 5 else 0
    if state.insider_activity and state.insider_activity.available:
        score += 1 if state.insider_activity.sentiment == "bullish" else 0
    summary: list[str] = []
    for officer in (state.market.officers if state.market else [])[:5]:
        name = officer.get("name") or "Data unavailable or unverifiable."
        title = officer.get("title") or "Data unavailable or unverifiable."
        summary.append(f"{name}: {title}.")
    if dilution is not None:
        summary.append(f"Share-count change was {dilution:.2f}% over the measured period.")
    unavailable = [
        "Board independence and committee detail.",
        "Compensation alignment.",
        "Acquisition execution history.",
        "Management credibility beyond source-backed officer and capital-allocation data.",
    ]
    return QualitativeAnalysisSection(score=_score10(score), summary=summary, data_unavailable=unavailable)


def _build_segment_analysis() -> QualitativeAnalysisSection:
    return QualitativeAnalysisSection(
        score=None,
        data_unavailable=[
            "Segment revenue, segment growth, segment profitability, market share, and valuation contribution require source-specific segment tables or investor materials not reliably parsed in this run."
        ],
    )


def _build_industry_analysis(state: AgentState) -> QualitativeAnalysisSection:
    summary = []
    if state.market and state.market.available:
        summary.append(f"Industry source: Yahoo Finance classifies the company in {state.market.industry or 'Data unavailable or unverifiable.'}.")
    if state.market and state.market.macro_notes:
        summary.extend(state.market.macro_notes)
    return QualitativeAnalysisSection(
        score=None,
        summary=summary,
        data_unavailable=[
            "TAM/SAM/SOM.",
            "Porter's Five Forces with source-backed market shares.",
            "Industry growth forecasts.",
            "Detailed regulatory and disruption landscape.",
        ],
    )


def _build_dcf_analysis(state: AgentState) -> DCFAnalysisSection:
    dcf = DCFAnalysisSection()
    if state.market and state.market.available and state.market.price and state.market.target_mean:
        expected = (state.market.target_mean / state.market.price - 1.0) * 100.0
        dcf.base_case.expected_return_pct = round(expected, 1)
        dcf.base_case.assumptions = ["Yahoo Finance mean analyst price target used as a market-implied reference, not a full DCF."]
        dcf.base_case.status = "Proxy valuation reference available; full DCF assumptions unavailable or unverifiable."
        dcf.margin_of_safety = f"{expected:+.1f}% versus current price using Yahoo Finance mean target."
    return dcf


def _enrich_risk_matrix(report: DDReport) -> None:
    metric_map = {
        "competition": ["Gross margin trend", "Revenue growth", "Market share disclosures"],
        "liquidity": ["Current ratio", "Operating cash flow", "Debt maturity disclosures"],
        "leverage": ["Debt/EBITDA", "Interest coverage when available", "Free cash flow"],
        "growth": ["Revenue growth", "FCF margin", "Analyst estimate revisions"],
        "valuation": ["Forward P/E", "EV/EBITDA", "Analyst target range"],
        "supply": ["Supplier concentration disclosures", "Inventory growth", "Gross margin trend"],
        "cyber": ["SEC cyber disclosures", "Incident disclosures", "Technology risk factors"],
    }
    for risk in report.risk_matrix:
        key = risk.title.lower()
        matched = next((metrics for needle, metrics in metric_map.items() if needle in key), None)
        risk.monitoring_metrics = risk.monitoring_metrics or matched or [
            "Latest SEC risk-factor updates",
            "Quarterly revenue and margin trend",
            "Management guidance changes",
        ]
        if risk.mitigation == "Data unavailable or unverifiable.":
            risk.mitigation = (
                "Monitor the listed metrics and require updated source evidence before changing the thesis."
            )


def _build_investment_thesis(state: AgentState, report: DDReport) -> InvestmentThesisSection:
    monitoring = [
        "Revenue growth",
        "Gross, operating, and net margin trend",
        "Free cash flow margin",
        "Debt/EBITDA and current ratio",
        "Analyst target and recommendation changes",
    ]
    return InvestmentThesisSection(
        bull_case=report.institutional_summary.key_bull_thesis,
        base_case=report.executive_summary[:3] and [c.text for c in report.executive_summary[:3]] or ["Data unavailable or unverifiable."],
        bear_case=report.institutional_summary.key_bear_thesis,
        probability_weighted_outcome=f"{report.institutional_summary.investment_rating} with {report.institutional_summary.confidence_score}/100 confidence.",
        monitoring_metrics=monitoring,
        upgrade_triggers=["Improving revenue growth and margin expansion.", "Lower leverage/liquidity risk.", "Positive revisions to analyst targets or guidance."],
        downgrade_triggers=["Margin compression.", "Deteriorating cash conversion.", "New high-severity risks or red flags."],
        exit_triggers=["Unverifiable accounting concerns become material.", "Liquidity or debt metrics breach risk thresholds.", "Thesis-critical growth assumptions fail."],
    )


def _build_quality_checks(state: AgentState, report: DDReport) -> ReportQualityChecks:
    score = _overall_score_100(state, report)
    dims: dict[str, float | int | str] = {"Overall Investment Score": score}
    if report.business_quality.score is not None:
        dims["Business Quality"] = report.business_quality.score
    if report.management_analysis.score is not None:
        dims["Management"] = report.management_analysis.score
    if state.scorecard and state.scorecard.available:
        for dim in state.scorecard.dimensions:
            dims[dim.name] = dim.score
    if report.earnings_quality.quality_label:
        dims["Earnings Quality"] = report.earnings_quality.quality_label
    return ReportQualityChecks(
        claim_verification=f"{len(report.all_claims())} claims verified by citations and/or deterministic metrics.",
        final_scorecard=dims,
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
        "fast", system, user, _WriterOutput, max_tokens=_WRITER_MAX_TOKENS
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
    report = DDReport(
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
    _backfill_missing_sections(report, state)
    _enrich_risk_matrix(report)
    report.business_quality = _build_business_quality(state)
    report.management_analysis = _build_management_analysis(state)
    report.segment_analysis = _build_segment_analysis()
    report.industry_analysis = _build_industry_analysis(state)
    report.dcf_analysis = _build_dcf_analysis(state)
    report.institutional_summary = _build_institutional_summary(state, report)
    report.investment_thesis = _build_investment_thesis(state, report)
    report.quality_checks = _build_quality_checks(state, report)
    return report


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
                "fast", load_prompt("writer_revision"), user, _Revision, max_tokens=500
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
    report = DDReport(
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
    _enrich_risk_matrix(report)
    report.business_quality = _build_business_quality(state)
    report.management_analysis = _build_management_analysis(state)
    report.segment_analysis = _build_segment_analysis()
    report.industry_analysis = _build_industry_analysis(state)
    report.dcf_analysis = _build_dcf_analysis(state)
    report.institutional_summary = _build_institutional_summary(state, report)
    report.investment_thesis = _build_investment_thesis(state, report)
    report.quality_checks = _build_quality_checks(state, report)
    return report


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


_canonical_evidence_block = _evidence_block
_canonical_synthetic_market_evidence = _synthetic_market_evidence
_canonical_w_claim = _WClaim
_canonical_w_risk = _WRisk
_canonical_writer_output = _WriterOutput
_canonical_revision = _Revision
_canonical_context_payload = _context_payload
_canonical_to_claims = _to_claims
_canonical_full_write = _full_write
_canonical_revise_claims = _revise_claims
_canonical_partial_report = _partial_report
_canonical_writer_node = writer_node

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
        "fast", system, user, _WriterOutput, max_tokens=_WRITER_MAX_TOKENS
    )

    valid_metric_ids = (
        {m.metric_id for m in state.analysis.metrics} if state.analysis else set()
    )
    valid_chunk_ids = {e.chunk_id for e in all_evidence}
    commentary = _to_claims(output.financial_commentary, "financial_health",
                            valid_metric_ids, valid_chunk_ids)
    if state.analysis and state.analysis.commentary:
        commentary.extend(state.analysis.commentary)

    report = DDReport(
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
    _backfill_missing_sections(report, state)
    return report


def _revise_claims(router: LLMRouter, state: AgentState) -> DDReport:
    assert state.report is not None
    report = state.report
    evidence_by_id = {e.chunk_id: e for e in state.evidence}
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


_evidence_block = _canonical_evidence_block
_synthetic_market_evidence = _canonical_synthetic_market_evidence
_WClaim = _canonical_w_claim
_WRisk = _canonical_w_risk
_WriterOutput = _canonical_writer_output
_Revision = _canonical_revision
_context_payload = _canonical_context_payload
_to_claims = _canonical_to_claims
_full_write = _canonical_full_write
_revise_claims = _canonical_revise_claims
_partial_report = _canonical_partial_report
writer_node = _canonical_writer_node
