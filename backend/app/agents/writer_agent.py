"""Writer agent: produces the structured DDReport (smart model).

First pass writes the full report via structured output. Revision passes
rewrite only the claims the critic rejected, using their cited chunks.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import settings
from app.llm.router import LLMRouter, load_prompt
from app.logging_setup import get_logger
from app.report.qa import truncate_words
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
            short_line = f"  Short interest: {m.short_percent_float*100:.2f}% of float"
            if m.shares_short and m.float_shares:
                short_line += (
                    f" ({m.shares_short:,.0f} shares short / {m.float_shares:,.0f} float shares)"
                )
            if m.short_interest_date:
                short_line += f", as of {m.short_interest_date}"
            lines.append(short_line)
        if m.business_summary:
            lines.append(f"  Business description: {m.business_summary[:600]}")
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
        # Per-article chunks so individual claims can cite the actual source
        # URL — index inclusion and similar binary facts stay checkable.
        for i, item in enumerate(state.news.items[:3]):
            pub = f" ({item.published_at.strftime('%Y-%m-%d')})" if item.published_at else ""
            chunks.append(RetrievedEvidence(
                chunk_id=f"{_MARKET_CHUNK_PREFIX}news_{i}",
                text=f"[{item.sentiment.upper()}] {item.title}{pub}"
                     + (f" — {item.snippet[:300]}" if item.snippet else ""),
                source_url=item.url,
                form_type="news",
                fiscal_period="current",
                section=f"News article {i + 1}",
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
            if state.market and state.market.available and state.market.market_cap:
                pct = abs(ia.net_value) / state.market.market_cap * 100.0
                scale = (
                    "routine in scale for a company this size"
                    if pct < 0.05
                    else "material relative to company size"
                )
                insider_lines.append(
                    f"  Scale context: net value is ~{pct:.4f}% of market cap - {scale}."
                )
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


class _WClaimList(BaseModel):
    items: list[_WClaim] = Field(default_factory=list)


class _WRiskList(BaseModel):
    items: list[_WRisk] = Field(default_factory=list)


_CLAIM_SECTIONS: tuple[str, ...] = (
    "executive_summary",
    "business_overview",
    "financial_commentary",
    "valuation_commentary",
    "earnings_quality_commentary",
    "insider_commentary",
    "recent_developments",
    "red_flags",
)


def _generate_writer_output(router: LLMRouter, system: str, user: str) -> _WriterOutput:
    """Escalating generation: fast monolithic → smart monolithic → per-section.

    A single failed monolithic call must never zero out the whole report —
    per-section generation tolerates individual section failures.
    """
    for tier in ("fast", "smart"):
        try:
            return router.complete_json(
                tier, system, user, _WriterOutput, max_tokens=_WRITER_MAX_TOKENS
            )
        except Exception as exc:
            log.warning("writer_monolithic_failed", tier=tier, error=str(exc)[:200])

    log.warning("writer_falling_back_to_per_section_generation")
    output = _WriterOutput()
    for section in _CLAIM_SECTIONS:
        try:
            result = router.complete_json(
                "fast",
                system,
                f"{user}\n\nGenerate ONLY the `{section}` section now: return a JSON object "
                f'{{"items": [...]}} with the claims for `{section}` per the section rules.',
                _WClaimList,
                max_tokens=600,
            )
            setattr(output, section, result.items)
        except Exception as exc:
            log.warning("writer_section_failed", section=section, error=str(exc)[:160])
    try:
        risks = router.complete_json(
            "fast",
            system,
            f"{user}\n\nGenerate ONLY the `risk_matrix` section now: return a JSON object "
            '{"items": [...]} with at least 3 risk entries per the section rules.',
            _WRiskList,
            max_tokens=900,
        )
        output.risk_matrix = risks.items
    except Exception as exc:
        log.warning("writer_section_failed", section="risk_matrix", error=str(exc)[:160])
    if not any(getattr(output, section) for section in _CLAIM_SECTIONS) and not output.risk_matrix:
        raise ValueError("Writer produced no valid content in any section after all fallbacks.")
    return output


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
    for item in items[:4]:
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

    # Target 4 claims pre-critic so verification drops still leave >= 3
    # (the institutional floor); QA tops up post-critic as a final backstop.
    if len(report.executive_summary) < 4:
        cited = {
            mid for claim in report.executive_summary for mid in claim.metric_ids
        }
        candidates: list[tuple[MetricValue | None, str]] = []
        rev = _find_metric(metrics, "revenue_growth_yoy")
        if rev is not None:
            direction = "expansion" if rev.value >= 0 else "contraction"
            candidates.append((
                rev,
                f"Revenue {'grew' if rev.value >= 0 else 'declined'} {abs(rev.value):.2f}% in "
                f"{rev.period}, indicating top-line {direction}.",
            ))
        om_trend = _find_metric(metrics, "operating_margin_trend_bps")
        if om_trend is not None:
            candidates.append((
                om_trend,
                f"Operating margin {'expanded' if om_trend.value >= 0 else 'contracted'} "
                f"{abs(om_trend.value):,.0f} bps YoY ({om_trend.period}), a key profitability signal.",
            ))
        fcf = _find_metric(metrics, "fcf")
        if fcf is not None:
            candidates.append((
                fcf,
                f"Free cash flow was {fcf.value:,.0f} {fcf.unit} in {fcf.period}, "
                "supporting financial flexibility.",
            ))
        fcf_margin_m = _find_metric(metrics, "fcf_margin")
        if fcf_margin_m is not None:
            candidates.append((
                fcf_margin_m,
                f"Free-cash-flow margin was {fcf_margin_m.value:.2f}% in {fcf_margin_m.period}, "
                "a measure of the cash backing behind reported growth.",
            ))
        current_m = _find_metric(metrics, "current_ratio")
        if current_m is not None:
            candidates.append((
                current_m,
                f"The balance sheet shows a current ratio of {current_m.value:.2f}x in "
                f"{current_m.period}, {'above' if current_m.value >= 1 else 'below'} the 1.0x liquidity threshold.",
            ))
        for metric, text in candidates:
            if len(report.executive_summary) >= 4:
                break
            if metric is None or metric.metric_id in cited:
                continue
            report.executive_summary.append(
                Claim(text=text, metric_ids=[metric.metric_id], section="executive_summary")
            )
            cited.add(metric.metric_id)
    if not report.business_overview:
        # 1st choice: 10-K Item 1 (Business) evidence — the authoritative source
        # for what the company does. Risk-factor chunks are excluded so peripheral
        # details (e.g. supplier codes of conduct) can't stand in for the model.
        business_chunks = [
            e for e in state.evidence
            if any(k in (e.section or "").lower() for k in ("business", "item 1", "overview"))
            and "risk" not in (e.section or "").lower()
        ]
        for chunk in business_chunks[:2]:
            excerpt = " ".join(chunk.text.split())
            cut = excerpt[:280]
            if " " in cut and len(excerpt) > 280:
                cut = cut.rsplit(" ", 1)[0] + "…"
            report.business_overview.append(
                Claim(
                    text=f"Per the 10-K business section: {cut}",
                    citation_chunk_ids=[chunk.chunk_id],
                    section="business_overview",
                )
            )
        # 2nd choice: the factual Yahoo Finance business description.
        if not report.business_overview:
            summary_text = (market.business_summary if market and market.available else "") or ""
            sentences = [s.strip() for s in summary_text.split(". ") if len(s.strip()) > 40]
            for sentence in sentences[:3]:
                text = sentence if sentence.endswith(".") else sentence + "."
                report.business_overview.append(
                    Claim(
                        text=text,
                        citation_chunk_ids=[f"{_MARKET_CHUNK_PREFIX}snapshot"],
                        section="business_overview",
                    )
                )
        if not report.business_overview:
            report.data_gaps.append(
                "Insufficient data for this section: Business Overview requires verified business-model and segment evidence."
            )
        else:
            gap = (
                "Segment revenue mix is unavailable: EDGAR's free companyfacts API excludes "
                "dimension-qualified segment facts, so per-segment revenue (e.g. Services vs "
                "Cloud) requires parsing the 10-K segment footnote, which this pipeline does "
                "not yet do."
            )
            if gap not in report.data_gaps:
                report.data_gaps.append(gap)

    # Recent developments come from news headlines — factual, source-attributed.
    if not report.recent_developments:
        news = state.news
        if news is not None and news.available and news.items:
            for i, item in enumerate(news.items[:3]):
                date = f" ({item.published_at.strftime('%Y-%m-%d')})" if item.published_at else ""
                report.recent_developments.append(
                    Claim(
                        text=f"News{date}, sentiment {item.sentiment}: {item.title.strip()}",
                        # Per-article chunk carries the direct source URL so
                        # binary facts (index inclusion etc.) stay checkable.
                        citation_chunk_ids=[f"{_MARKET_CHUNK_PREFIX}news_{i}"],
                        section="recent_developments",
                    )
                )
        elif news is not None and not news.available:
            # Pipeline failure, not an absence of news — say which it was.
            report.metadata.warnings.append(
                "PIPELINE: news fetch returned nothing "
                f"({news.error or 'unknown error'}) - 'no recent developments' reflects a "
                "failed news pipeline, not an absence of coverage."
            )
        else:
            report.data_gaps.append(
                "Recent Developments: no material news items passed the relevance filter this run."
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

        # Metric-driven risks only; _ensure_structural_risks tops up to 3 with
        # richer structural candidates, so no duplicate filler here (duplicated
        # regulatory entries used to double-count the -8 severity deduction).
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

    # 'No red flags' must not conflate 'checked and clean' with 'couldn't check'.
    if not (state.insider_activity and state.insider_activity.available):
        note = (
            "Red-flag coverage note: insider-activity data was unavailable this run, so "
            "insider-based red-flag screening did not execute - an empty Red Flags section "
            "reflects reduced coverage, not a full all-clear."
        )
        if note not in report.data_gaps:
            report.data_gaps.append(note)


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
    """Populate the valuation section from the market snapshot with range checks."""
    if state.market is None or not state.market.available:
        return ValuationSection()
    m = state.market

    dividend_yield = m.dividend_yield
    if dividend_yield is not None and not 0 <= dividend_yield <= 0.10:
        log.warning("valuation_dividend_yield_outlier_omitted", ticker=state.ticker, value=dividend_yield)
        dividend_yield = None
    beta = m.beta
    if beta is not None and not -5 <= beta <= 5:
        log.warning("valuation_beta_outlier_omitted", ticker=state.ticker, value=beta)
        beta = None
    short_percent_float = m.short_percent_float
    if short_percent_float is not None and not 0 <= short_percent_float <= 1.0:
        log.warning("valuation_short_interest_outlier_omitted", ticker=state.ticker, value=short_percent_float)
        short_percent_float = None

    return ValuationSection(
        price=m.price,
        market_cap=m.market_cap,
        pe_ttm=m.pe_ttm,
        forward_pe=m.forward_pe,
        ev_to_ebitda=m.ev_to_ebitda,
        price_to_sales=m.price_to_sales,
        price_to_book=m.price_to_book,
        beta=beta,
        target_mean=m.target_mean,
        target_high=m.target_high,
        target_low=m.target_low,
        recommendation=m.recommendation,
        recommendation_mean=m.recommendation_mean,
        num_analysts=m.num_analysts,
        short_percent_float=short_percent_float,
        short_ratio=m.short_ratio,
        shares_short=m.shares_short,
        float_shares=m.float_shares,
        short_interest_date=m.short_interest_date,
        dividend_yield=dividend_yield,
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


def _has_core_valuation(state: AgentState) -> bool:
    market = state.market
    if market is None or not market.available:
        return False
    if any(
        value is not None and value > 0
        for value in (market.pe_ttm, market.forward_pe, market.ev_to_ebitda, market.price_to_sales)
    ):
        return True
    return any(
        peer.pe_ttm is not None or peer.ev_to_ebitda is not None
        for peer in (market.peers or [])
    )


def _coverage_checks(state: AgentState, report: DDReport | None = None) -> list[tuple[str, bool]]:
    """Labeled coverage checks — the same list drives the ratio and the
    human-readable decomposition, so 'Coverage Ratio 0.79' is explainable."""
    checks: list[tuple[str, bool]] = [
        ("financial metrics (XBRL)", bool(state.analysis and state.analysis.metrics)),
        ("SEC filing evidence", bool(state.evidence)),
        ("market snapshot", bool(state.market and state.market.available)),
        ("core valuation multiples", _has_core_valuation(state)),
        ("news feed", bool(state.news and state.news.available and state.news.items)),
        ("insider activity", bool(state.insider_activity and state.insider_activity.available)),
    ]
    if report is not None:
        checks.extend([
            ("executive summary", bool(report.executive_summary)),
            ("business overview", bool(report.business_overview)),
            ("financial commentary", bool(report.financial_health.commentary or report.financial_health.table.metrics)),
            ("valuation analysis", bool(report.valuation.commentary or _has_core_valuation(state))),
            ("risk matrix", bool(report.risk_matrix)),
            ("recent developments", bool(report.recent_developments)),
            ("bull thesis", bool(report.institutional_summary.key_bull_thesis)),
            ("bear thesis", bool(report.institutional_summary.key_bear_thesis)),
        ])
    return checks


def _coverage_ratio(state: AgentState, report: DDReport | None = None) -> float:
    checks = _coverage_checks(state, report)
    return sum(1 for _, ok in checks if ok) / len(checks)


def _coverage_gaps(state: AgentState, report: DDReport | None = None) -> list[str]:
    return [name for name, ok in _coverage_checks(state, report) if not ok]


def _overall_score_100(state: AgentState, report: DDReport | None = None) -> int:
    if state.scorecard and state.scorecard.available:
        base = state.scorecard.composite_score / 5.0 * 100
    else:
        base = 50.0
    risk_penalty = 0.0
    if report is not None:
        risk_penalty += 8.0 * sum(1 for risk in report.risk_matrix if risk.severity == "high")
        risk_penalty += 4.0 * len(report.red_flags)
    if not _has_core_valuation(state):
        risk_penalty += 10.0
    return max(0, min(100, round(base - risk_penalty)))


def _rating_from_score(score: int, state: AgentState) -> str:
    if not _has_core_valuation(state):
        return "Neutral / Insufficient Data"
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
    """Auditable expected-return string: always states price, targets, and analyst count."""
    market = state.market
    if market is None or not market.available or not market.price:
        return "Data unavailable or unverifiable."
    analysts = f", {market.num_analysts} analysts" if market.num_analysts else ""
    if market.target_low and market.target_high and market.target_mean:
        low = (market.target_low / market.price - 1.0) * 100.0
        high = (market.target_high / market.price - 1.0) * 100.0
        mean = (market.target_mean / market.price - 1.0) * 100.0
        return (
            f"12-month basis: {low:+.0f}% to {high:+.0f}% (mean {mean:+.0f}%) vs price "
            f"${market.price:,.2f}; analyst targets low ${market.target_low:,.0f} / mean "
            f"${market.target_mean:,.0f} / high ${market.target_high:,.0f} "
            f"(Yahoo Finance{analysts})."
        )
    if market.target_mean:
        expected = (market.target_mean / market.price - 1.0) * 100.0
        return (
            f"12-month basis: {expected:+.0f}% vs price ${market.price:,.2f}; mean analyst "
            f"target ${market.target_mean:,.0f} (Yahoo Finance{analysts})."
        )
    return "Data unavailable or unverifiable."


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


_THESIS_TOPICS = (
    "capex intensity", "operating margin", "gross margin", "cash conversion",
    "mean-reversion", "forward p/e", "insider", "short interest",
)


def _append_topic_unique(items: list[str], value: str) -> None:
    """Append unless another item already covers the same topic — prevents
    e.g. the capex anomaly and the capex bear point appearing side by side."""
    lower = value.lower()
    topic = next((t for t in _THESIS_TOPICS if t in lower), None)
    if topic and any(topic in existing.lower() for existing in items):
        return
    _append_unique(items, value)


def _scenario_weighted_outcome(state: AgentState) -> str:
    """Probability-weighted 12-month outcome from analyst target scenarios.

    Deterministic Python arithmetic over Yahoo Finance targets — never LLM math.
    """
    market = state.market
    if not (
        market
        and market.available
        and market.price
        and market.target_mean
        and market.target_low
        and market.target_high
    ):
        return "Not assessed - insufficient valuation data for scenario weighting."
    price = market.price
    bear = (market.target_low / price - 1.0) * 100.0
    base = (market.target_mean / price - 1.0) * 100.0
    bull = (market.target_high / price - 1.0) * 100.0
    weighted = 0.25 * bear + 0.50 * base + 0.25 * bull
    return (
        f"Probability-weighted 12-month return ~ {weighted:+.0f}% "
        f"(bear 25%: {bear:+.0f}% at ${market.target_low:,.0f} | "
        f"base 50%: {base:+.0f}% at ${market.target_mean:,.0f} | "
        f"bull 25%: {bull:+.0f}% at ${market.target_high:,.0f}; "
        f"scenarios anchored to Yahoo Finance analyst targets vs price ${price:,.2f})."
    )


def _insider_scale_note(state: AgentState) -> tuple[str, float | None]:
    """(context sentence, |net value| as % of market cap) for insider activity."""
    ia = state.insider_activity
    market = state.market
    if ia is None or not ia.available or not ia.net_value:
        return "", None
    if market and market.available and market.market_cap:
        pct = abs(ia.net_value) / market.market_cap * 100.0
        scale = "modest relative to company size" if pct < 0.05 else "material in scale"
        return (
            f"net ${abs(ia.net_value)/1e6:,.1f}M, ~{pct:.4f}% of market cap - {scale}",
            pct,
        )
    return f"net ${abs(ia.net_value)/1e6:,.1f}M", None


def _confidence_from_coverage(state: AgentState, report: DDReport,
                              bull: list[str], bear: list[str]) -> int:
    """Confidence derived from evidence coverage and section quality — never 100.

    30-75 base from data coverage, penalties for a thin bear case, a thin risk
    matrix, missing core valuation, and disclosed data gaps. Capped at 85.
    """
    coverage = _coverage_ratio(state, report)
    confidence = 30 + round(45 * coverage)
    if len(bear) < 2:
        confidence -= 10
    if len(report.risk_matrix) < 3:
        confidence -= 5
    if not _has_core_valuation(state):
        confidence -= 10
    confidence -= min(10, 2 * len(state.data_gaps))
    return max(20, min(85, confidence))


def _build_institutional_summary(state: AgentState, report: DDReport) -> InstitutionalExecutiveSummary:
    score = _overall_score_100(state, report)
    market = state.market

    bull: list[str] = []
    bear: list[str] = []
    catalysts: list[str] = []
    risks: list[str] = []

    rev_growth = _find_metric(state.analysis.metrics if state.analysis else [], "revenue_growth_yoy")
    fcf_margin = _metric_value(state, "fcf_margin")
    roic = _metric_value(state, "roic")
    debt = _metric_value(state, "debt_to_ebitda")
    current = _metric_value(state, "current_ratio")
    dilution = _metric_value(state, "share_dilution")
    gm_trend = _metric_value(state, "gross_margin_trend_bps")
    om_trend = _metric_value(state, "operating_margin_trend_bps")
    cash_conv = _metric_value(state, "cash_conversion")

    # ── Bull: substantive, thesis-style drivers ────────────────────────────
    if rev_growth is not None:
        if rev_growth.value > 0:
            bull.append(
                f"Top-line momentum: revenue grew {rev_growth.value:.1f}% in {rev_growth.period}, "
                "supporting the durability of the core franchise."
            )
        else:
            bear.append(
                f"Revenue declined {abs(rev_growth.value):.1f}% in {rev_growth.period}, "
                "pressuring the growth thesis."
            )
    if om_trend is not None and om_trend > 0:
        bull.append(
            f"Operating leverage: operating margin expanded {om_trend:+,.0f} bps YoY, "
            "evidence of cost discipline scaling with revenue."
        )
    elif gm_trend is not None and gm_trend > 0:
        bull.append(f"Gross margin expanded {gm_trend:+,.0f} bps YoY, supporting profitability durability.")
    if fcf_margin is not None and fcf_margin > 5:
        bull.append(
            f"Cash generation: free-cash-flow margin of {fcf_margin:.1f}% funds reinvestment "
            "and capital returns without leverage."
        )
    if dilution is not None and dilution < 0:
        bull.append(
            f"Shareholder-friendly capital allocation: diluted share count fell {abs(dilution):.1f}% "
            "over the measured period via repurchases."
        )
    if roic is not None and roic > 10:
        bull.append(f"Returns on capital: ROIC of {roic:.1f}% indicates value-creating reinvestment.")
    if debt is not None and debt <= 2.5 and current is not None and current >= 1.0:
        bull.append(
            f"Balance-sheet strength: Debt/EBITDA of {debt:.2f}x and current ratio of {current:.2f}x "
            "provide strategic flexibility."
        )

    # ── Bear: substantive, evidence-linked downside drivers ───────────────
    if om_trend is not None and om_trend < 0:
        bear.append(f"Operating margin contracted {om_trend:+,.0f} bps YoY - a margin-pressure signal.")
    if fcf_margin is not None and fcf_margin <= 5:
        bear.append(f"Thin free-cash-flow margin ({fcf_margin:.1f}%) limits downside cushion.")
    if debt is not None and debt > 2.5:
        bear.append(f"Leverage of {debt:.2f}x Debt/EBITDA raises refinancing and rate sensitivity.")
    if current is not None and current < 1.0:
        bear.append(f"Current ratio of {current:.2f}x is below 1.0x, a near-term liquidity constraint.")
    if cash_conv is not None and cash_conv < 0.8:
        bear.append(
            f"Cash conversion of {cash_conv:.2f}x means reported earnings are not fully cash-backed."
        )
    for anomaly in (state.analysis.anomalies if state.analysis else [])[:2]:
        _append_topic_unique(bear, anomaly)
    for risk in report.risk_matrix:
        if risk.severity == "high":
            # Only pair the claim with the title when they actually match —
            # a content-moderation claim must not appear under a regulatory label.
            first_claim = risk.claims[0].text if risk.claims else ""
            title_words = {w for w in re.findall(r"[a-z]{5,}", risk.title.lower())}
            claim_matches_title = bool(first_claim) and any(
                w in first_claim.lower() for w in title_words
            )
            _append_topic_unique(
                bear,
                f"{risk.title} (high severity): {first_claim}" if claim_matches_title
                else f"{risk.title} is rated high severity - see Risk Matrix.",
            )
    for flag in report.red_flags[:2]:
        _append_unique(bear, flag.text)
    if market and market.available and market.forward_pe and market.forward_pe > 25:
        bear.append(
            f"Valuation embeds high expectations (forward P/E {market.forward_pe:.1f}x); "
            "multiple compression is a key downside if growth or margins disappoint."
        )
    capex_int = _metric_value(state, "capex_intensity")
    if capex_int is not None and capex_int > 10:
        _append_topic_unique(
            bear,
            f"Capex intensity of {capex_int:.1f}% of revenue pressures forward free-cash-flow "
            "margins if returns on that investment lag.",
        )
    if market and market.available and market.change_1y_pct is not None and market.change_1y_pct > 50:
        reversion = (
            f"Shares are up {market.change_1y_pct:+.0f}% over the trailing 12 months, "
            "raising mean-reversion risk after an extended move"
        )
        if market.price and market.target_low and market.price > market.target_low:
            reversion += (
                f"; the current price (${market.price:,.2f}) already exceeds the low analyst "
                f"target (${market.target_low:,.0f})"
            )
        bear.append(reversion + ".")
    if state.news and state.news.available and state.news.items:
        negatives = [n for n in state.news.items if n.sentiment == "negative"]
        if negatives:
            bear.append(
                f"{len(negatives)} of {len(state.news.items)} recent headlines carry negative "
                f"sentiment, led by: {negatives[0].title[:110]}."
            )
    insider_note, insider_pct = _insider_scale_note(state)
    if (
        state.insider_activity
        and state.insider_activity.available
        and state.insider_activity.sentiment == "bearish"
        and insider_pct is not None
        and insider_pct >= 0.05
    ):
        bear.append(f"Sustained net insider selling over the trailing 12 months ({insider_note}).")

    if not _has_core_valuation(state):
        _append_unique(
            bear,
            "Core valuation multiples or peer benchmarks are unavailable, so the rating is "
            "constrained to Neutral / Insufficient Data.",
        )
    if len(bull) < 2:
        bull.append("Limited additional verified bull drivers this run - see Financial Health metrics.")
    if len(bear) < 3:
        bear.append("Limited additional verified bear drivers this run - see Risk Matrix and Data Gaps.")

    # ── Catalysts and top risks ────────────────────────────────────────────
    for risk in report.risk_matrix[:3]:
        _append_unique(risks, risk.title)
    if state.news and state.news.available and state.news.items:
        positives = [n for n in state.news.items if n.sentiment == "positive"]
        if positives:
            catalysts.append(f"News flow: {positives[0].title[:110]}.")
    if market and market.available and market.target_mean and market.price:
        catalysts.append("Analyst target and estimate revisions (targets currently anchor the expected-return range).")
    if any("regulat" in r.title.lower() or "antitrust" in r.title.lower() for r in report.risk_matrix):
        catalysts.append("Resolution or escalation of pending regulatory/antitrust proceedings.")
    if market and market.available and market.change_1y_pct is not None:
        catalysts.append(
            f"Momentum follow-through or reversal versus the {market.change_1y_pct:+.1f}% trailing-12-month move."
        )
    while len(catalysts) < 3:
        catalysts.append("Updated filings, market data, or news could materially change the thesis.")
    while len(risks) < 3:
        risks.append("Material risk coverage requires continued review of filings, news, valuation, and liquidity indicators.")

    rating = _rating_from_score(score, state)
    expected_return = _expected_return_range(state)
    if rating != "Neutral / Insufficient Data" and expected_return == "Data unavailable or unverifiable.":
        rating = "Neutral / Insufficient Data"

    confidence = _confidence_from_coverage(state, report, bull, bear)

    return InstitutionalExecutiveSummary(
        investment_rating=rating,
        confidence_score=confidence,
        investment_horizon="3-year thesis (returns quoted on a 12-month analyst-target basis)",
        key_bull_thesis=bull[:5],
        key_bear_thesis=bear[:5],
        top_catalysts=catalysts[:3],
        top_risks=risks[:3],
        expected_return_range=expected_return,
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


_ANTITRUST_KEYWORDS = (
    "antitrust", "doj", "department of justice", "ftc", "monopol", "remedy",
    "remedies", "divestiture", "european commission", "competition authority",
)
_DISRUPTION_KEYWORDS = ("ai ", " ai", "artificial intelligence", "disrupt", "chatgpt", "competitor")


def _news_matching(state: AgentState, keywords: tuple[str, ...]) -> list[Any]:
    if not (state.news and state.news.available and state.news.items):
        return []
    return [
        item for item in state.news.items
        if any(k in item.title.lower() for k in keywords)
    ]


def _filing_evidence_claim(state: AgentState, keywords: tuple[str, ...]) -> Claim | None:
    """Verbatim sentence from a filing chunk containing one of the keywords,
    cited to that chunk — survives NLI verification because it is a quote."""
    for chunk in state.evidence:
        lower = chunk.text.lower()
        for keyword in keywords:
            idx = lower.find(keyword)
            if idx == -1:
                continue
            start = chunk.text.rfind(".", 0, idx) + 1
            end = chunk.text.find(".", idx)
            end = end + 1 if end != -1 else min(len(chunk.text), idx + 240)
            sentence = " ".join(chunk.text[start:end].split()).strip()
            if len(sentence) < 40:
                continue
            return Claim(
                text=f"Per the company's SEC filings: {sentence[:300]}",
                citation_chunk_ids=[chunk.chunk_id],
                section="risk_matrix",
            )
    return None


def _calibrate_risk_severity(report: DDReport, state: AgentState) -> None:
    """Upgrade regulatory/antitrust severity when active proceedings are evidenced.

    'Medium' for a company under active structural-remedy litigation understates
    risk and inflates the score (only high severity takes the -8 deduction).
    The upgrade requires evidence: antitrust keywords in filings or news. The
    highest-graded risk must never be the one without a sourced claim, so an
    evidence citation (news headline or verbatim filing sentence) is attached.
    """
    evidence_text = " ".join(e.text.lower() for e in state.evidence)
    antitrust_news = _news_matching(state, _ANTITRUST_KEYWORDS)
    evidenced = bool(antitrust_news) or any(k in evidence_text for k in _ANTITRUST_KEYWORDS)
    if not evidenced:
        return
    for risk in report.risk_matrix:
        title = risk.title.lower()
        if "regulat" not in title and "antitrust" not in title:
            continue
        upgraded = False
        if risk.severity != "high":
            risk.severity = "high"
            upgraded = True
        if not risk.claims:
            if antitrust_news:
                risk.claims.append(
                    Claim(
                        text=(
                            "Active regulatory/antitrust proceedings are evidenced in current "
                            f"news flow: {antitrust_news[0].title[:140]}"
                        ),
                        citation_chunk_ids=[f"{_MARKET_CHUNK_PREFIX}news"],
                        section="risk_matrix",
                    )
                )
            else:
                filing_claim = _filing_evidence_claim(state, _ANTITRUST_KEYWORDS)
                if filing_claim is not None:
                    risk.claims.append(filing_claim)
        if upgraded:
            log.info("risk_severity_upgraded", title=risk.title, reason="antitrust evidence")


def _ensure_structural_risks(report: DDReport, state: AgentState) -> None:
    """Guarantee a useful minimum risk section without fabricating numeric facts.

    Structural candidates carry real mitigants (not risk restatements) and cite
    company-specific news evidence where keyword matches exist.
    """
    existing = {risk.title.lower() for risk in report.risk_matrix}

    def cited_claims(keywords: tuple[str, ...], prefix: str) -> list[Claim]:
        matches = _news_matching(state, keywords)
        if not matches:
            return []
        return [
            Claim(
                text=f"{prefix}: {matches[0].title[:150]}",
                citation_chunk_ids=[f"{_MARKET_CHUNK_PREFIX}news"],
                section="risk_matrix",
            )
        ]

    candidates = [
        RiskEntry(
            title="Regulatory and Antitrust Pressure",
            severity="medium",  # _calibrate_risk_severity upgrades when evidenced
            likelihood="medium",
            claims=cited_claims(_ANTITRUST_KEYWORDS, "Current regulatory news flow"),
            mitigation=(
                "Mitigants: revenue diversification into cloud and subscriptions reduces reliance "
                "on the practices under scrutiny; balance-sheet capacity can absorb fines without "
                "impairing operations; structural remedies typically phase in over years, allowing "
                "the business model to adapt."
            ),
            monitoring_metrics=["Remedy hearing dates and rulings", "DOJ/EC appeal timeline", "SEC legal proceedings disclosures"],
        ),
        RiskEntry(
            title="Competitive and Technology Disruption",
            severity="medium",
            likelihood="medium",
            claims=cited_claims(_DISRUPTION_KEYWORDS, "Disruption-related news flow"),
            mitigation=(
                "Mitigants: sustained R&D investment and first-party distribution defend the core "
                "franchise; cash generation funds competitive responses; entrenched user bases "
                "raise switching costs for challengers."
            ),
            monitoring_metrics=["Revenue growth vs peers", "R&D intensity", "Usage/engagement disclosures"],
        ),
        RiskEntry(
            title="Revenue Concentration and Cyclicality",
            severity="medium",
            likelihood="low",
            mitigation=(
                "Mitigants: growth of secondary segments dilutes dependence on the primary revenue "
                "line over time; geographic spread cushions regional downturns; recurring/subscription "
                "revenue is less cyclical than transactional demand."
            ),
            monitoring_metrics=["Segment revenue mix", "Top-customer/product concentration disclosures", "Free cash flow through the cycle"],
        ),
    ]
    for risk in candidates:
        if len(report.risk_matrix) >= 3:
            break
        if risk.title.lower() not in existing:
            report.risk_matrix.append(risk)
            existing.add(risk.title.lower())
    _calibrate_risk_severity(report, state)


def _enrich_risk_matrix(report: DDReport) -> None:
    """Attach risk-specific monitoring metrics and mitigations (no shared boilerplate)."""
    playbook: dict[str, tuple[list[str], str]] = {
        "antitrust": (
            ["Remedy hearing dates and rulings", "DOJ/EC appeal timeline", "Divestiture or conduct-remedy scope"],
            "Track remedy proceedings, appeal milestones, and any divestiture or conduct requirements; "
            "reassess the thesis when a ruling defines remedy scope.",
        ),
        "regulat": (
            ["SEC legal proceedings disclosures", "Regulatory enforcement updates", "Material 8-K filings"],
            "Track docketed proceedings, enforcement actions, and new legislation in filings; "
            "quantify exposure when penalties or remedies are disclosed.",
        ),
        "content": (
            ["Platform policy or enforcement changes", "Advertiser sentiment and brand-safety incidents", "User engagement disclosures"],
            "Monitor content-policy incidents, advertiser responses, and engagement trends that could "
            "affect monetization or invite regulation.",
        ),
        "reputat": (
            ["Brand-safety incidents", "Advertiser or customer churn signals", "Litigation and press coverage"],
            "Watch for incidents that convert reputational pressure into measurable revenue or legal exposure.",
        ),
        "competit": (
            ["Gross margin trend", "Revenue growth vs peers", "Market share disclosures"],
            "Compare growth and margin trends against named peers each quarter; treat sustained share "
            "loss or pricing pressure as a thesis-change trigger.",
        ),
        "technology": (
            ["R&D intensity", "Product launch cadence", "Competitive win/loss disclosures"],
            "Track product-cycle execution and disclosed competitive responses; disruption risks compound "
            "when growth and margins weaken together.",
        ),
        "disrupt": (
            ["R&D intensity", "New-entrant traction signals", "Usage/engagement disclosures"],
            "Monitor adoption of substitute products and management's disclosed counter-investments.",
        ),
        "liquidity": (
            ["Current ratio", "Operating cash flow", "Debt maturity schedule"],
            "Track working-capital trends and upcoming maturities; a current ratio below 1.0x or negative "
            "OCF would escalate this risk.",
        ),
        "leverage": (
            ["Debt/EBITDA", "Interest coverage", "Free cash flow"],
            "Watch leverage against covenant and rating thresholds; refinancing needs in a higher-rate "
            "environment are the key stress scenario.",
        ),
        "growth": (
            ["Revenue growth", "FCF margin", "Analyst estimate revisions"],
            "Treat two consecutive quarters of decelerating revenue plus estimate cuts as a downgrade trigger.",
        ),
        "valuation": (
            ["Forward P/E vs peers", "EV/EBITDA vs history", "Analyst target range"],
            "Reassess when multiples de-rate toward peer or historical averages without a fundamental driver.",
        ),
        "concentr": (
            ["Segment revenue mix", "Top-customer disclosures", "Geographic mix"],
            "Monitor disclosed concentration metrics; diversification progress reduces this risk over time.",
        ),
        "cycl": (
            ["Macro indicators (rates, ad spend, consumer demand)", "Segment revenue mix", "Free cash flow"],
            "Track demand-sensitive segments against macro data; cyclicality is mitigated by balance-sheet strength.",
        ),
        "supply": (
            ["Supplier concentration disclosures", "Inventory growth", "Gross margin trend"],
            "Monitor supplier disclosures and inventory anomalies for early signs of disruption or cost pass-through.",
        ),
        "cyber": (
            ["SEC cyber incident disclosures", "8-K incident filings", "Technology risk-factor updates"],
            "Watch incident disclosures and remediation spend; a material breach is an immediate review trigger.",
        ),
    }
    generic_mitigation = (
        "Monitor the listed metrics and require updated source evidence before changing the thesis."
    )
    for risk in report.risk_matrix:
        key = risk.title.lower()
        matched = next(
            ((metrics, mitigation) for needle, (metrics, mitigation) in playbook.items() if needle in key),
            None,
        )
        if not risk.monitoring_metrics:
            risk.monitoring_metrics = matched[0] if matched else [
                "Latest SEC risk-factor updates",
                "Quarterly revenue and margin trend",
                "Management guidance changes",
            ]
        if risk.mitigation in ("Data unavailable or unverifiable.", generic_mitigation, ""):
            risk.mitigation = matched[1] if matched else generic_mitigation


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
        probability_weighted_outcome=_scenario_weighted_outcome(state),
        monitoring_metrics=monitoring,
        upgrade_triggers=["Improving revenue growth and margin expansion.", "Lower leverage/liquidity risk.", "Positive revisions to analyst targets or guidance."],
        downgrade_triggers=["Margin compression.", "Deteriorating cash conversion.", "New high-severity risks or red flags."],
        exit_triggers=["Unverifiable accounting concerns become material.", "Liquidity or debt metrics breach risk thresholds.", "Thesis-critical growth assumptions fail."],
    )


def _build_quality_checks(state: AgentState, report: DDReport) -> ReportQualityChecks:
    score = _overall_score_100(state, report)
    dims: dict[str, float | int | str] = {"Overall Investment Score (0-100)": score}
    if state.scorecard and state.scorecard.available:
        n_dims = len(state.scorecard.dimensions)
        dims["Formula"] = (
            f"Overall Investment Score = composite / 5 * 100 minus 8 points per "
            f"high-severity risk, 4 points per red flag, and 10 points when core "
            f"valuation data is unavailable. Composite = unweighted mean of the "
            f"{n_dims} dimension scores below."
        )
        dims[f"Composite (mean of {n_dims} dimensions, /5)"] = round(
            state.scorecard.composite_score, 2
        )
        for dim in state.scorecard.dimensions:
            dims[f"{dim.name} (/5)"] = dim.score
    else:
        dims["Formula"] = (
            "Overall Investment Score = neutral base 50 minus risk, red-flag, "
            "and missing-valuation penalties because no scorecard was available."
        )
    if report.business_quality.score is not None:
        dims["Business Quality (/10, informational - not in composite)"] = report.business_quality.score
    if report.management_analysis.score is not None:
        dims["Management (/10, informational - not in composite)"] = report.management_analysis.score
    if report.earnings_quality.quality_label:
        dims["Earnings Quality label"] = report.earnings_quality.quality_label
    coverage_checks = _coverage_checks(state, report)
    dims["Coverage Ratio (data completeness, 0-1)"] = round(_coverage_ratio(state, report), 2)
    dims["Coverage Ratio definition"] = (
        f"Share of {len(coverage_checks)} source-input and section checks that passed. "
        "Optional sections that are legitimately empty after their checks ran "
        "(e.g. Red Flags with no findings) are excluded by design; unavailable "
        "inputs are counted and also disclosed under Data Gaps."
    )
    gaps = _coverage_gaps(state, report)
    if gaps:
        dims["Coverage gaps (the missing share of the ratio)"] = ", ".join(gaps)
    medium_reg = [
        r for r in report.risk_matrix
        if r.severity == "medium" and ("regulat" in r.title.lower() or "antitrust" in r.title.lower())
    ]
    if medium_reg:
        dims["Severity sensitivity"] = (
            f"Overall score would be {max(0, score - 8 * len(medium_reg))} if "
            f"{'; '.join(r.title for r in medium_reg)} were graded high severity "
            "(-8 per high-severity risk)."
        )
    dims["Confidence formula"] = (
        "Confidence = 30 + 45 x coverage ratio, minus 10 if the bear case has fewer than "
        "2 substantive items, 5 if the risk matrix has fewer than 3 entries, 10 when core "
        "valuation is unavailable, and 2 per data gap (max 10); bounded to 20-85. "
        "Fewer than 3 verified claims caps confidence at 35 and forces a Neutral rating."
    )
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
    output = _generate_writer_output(router, system, user)

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
    if insider_section.available and not insider_section.commentary:
        transaction_count = len(insider_section.transactions)
        if transaction_count == 1:
            interpretation = "The single reported insider transaction is insufficient to establish a persistent directional signal."
        elif insider_section.net_shares < 0:
            interpretation = "Repeated net insider selling is a potentially bearish signal, although transaction motives cannot be determined from Form 4 data alone."
        elif insider_section.net_shares > 0:
            interpretation = "Net insider buying is a potentially positive alignment signal, while transaction size and context should still be monitored."
        else:
            interpretation = "Reported insider transactions are balanced and do not establish a clear directional signal."
        insider_section.commentary = [
            Claim(
                text=interpretation,
                citation_chunk_ids=[f"{_MARKET_CHUNK_PREFIX}insiders"],
                section="insider_activity",
            )
        ]

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
            for risk in output.risk_matrix[:4]
        ],
        recent_developments=(
            _to_claims(output.recent_developments, "recent_developments",
                       valid_metric_ids, valid_chunk_ids)
            if state.news and state.news.available and state.news.items
            else []
        ),
        red_flags=_to_claims(output.red_flags, "red_flags", valid_metric_ids, valid_chunk_ids),
        management_questions=list(state.management_questions),
        data_gaps=list(state.data_gaps),
        metadata=ReportMetadata(run_id=state.run_id),
    )
    _backfill_missing_sections(report, state)
    _ensure_structural_risks(report, state)
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
        except Exception as exc:
            log.warning("revision_failed_dropping_claim", claim_id=claim.claim_id,
                        error=str(exc)[:300])
            revision = _Revision(action="drop")

        if revision.action == "drop" or not revision.text.strip():
            report.drop_claim(claim.claim_id)
            # Pipeline QA note, not client content — surfaces in the appendix.
            report.metadata.warnings.append(
                f"Claim dropped during revision (unsupported): {truncate_words(claim.text, 140)}"
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
        data_gaps=list(state.data_gaps),
        metadata=ReportMetadata(
            run_id=state.run_id,
            partial=True,
            warnings=[
                "PIPELINE ERROR: report writer could not produce validated structured "
                "output after all fallbacks; prose sections were built deterministically."
            ],
        ),
    )
    # Deterministic backfills so a writer failure never ships hollow sections.
    _backfill_missing_sections(report, state)
    _ensure_structural_risks(report, state)
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
    log.info(
        "writer_node_start",
        mode="write" if state.report is None else "revise",
        evidence=len(state.evidence),
        data_gaps=len(state.data_gaps),
    )
    if state.report is None:
        try:
            log.info("writer_full_report_start", ticker=state.ticker)
            report = _full_write(router, state)
        except Exception as exc:
            log.error("writer_failed_shipping_partial", error=str(exc)[:300])
            return {
                "report": _partial_report(state, str(exc)[:200]),
                "status": "written_partial",
            }
        log.info("writer_full_report_end", ticker=state.ticker, claims=len(report.all_claims()))
        log.info("report_written", claims=len(report.all_claims()))
        return {"report": report, "status": "written"}

    log.info("writer_revision_start", revision=state.revision_count + 1)
    report = _revise_claims(router, state)
    revision = state.revision_count + 1
    log.info("writer_revision_end", revision=revision, claims=len(report.all_claims()))
    log.info("report_revised", revision=revision,
             max_revisions=settings.critic_max_revisions)
    return {"report": report, "revision_count": revision, "status": "revised"}

