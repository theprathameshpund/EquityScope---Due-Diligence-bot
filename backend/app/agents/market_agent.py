"""Market agent: yfinance snapshot + peer multiples + analyst consensus +
short interest + insider activity + optional FRED macro + investment scorecard.

Pure tool calls except a one-paragraph neutral summary (fast model) and
peer ticker suggestion. Any failure degrades gracefully — never fails the run.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from app.llm.router import LLMRouter, load_prompt
from app.logging_setup import get_logger
from app.state import (
    AgentState,
    InvestmentScorecard,
    MarketSnapshot,
    ScorecardDimension,
)
from app.tools.market_data import (
    get_insider_activity,
    get_macro_notes,
    get_peer_multiples,
    get_snapshot,
)

log = get_logger(__name__)


class _Peers(BaseModel):
    tickers: list[str] = Field(min_length=1, max_length=5)


PEER_FALLBACKS: dict[str, list[str]] = {
    "AMZN": ["WMT", "COST", "TGT", "EBAY", "BABA"],
    "WMT": ["COST", "TGT", "AMZN", "DG", "KR"],
    "MSFT": ["AAPL", "GOOGL", "ORCL", "CRM", "ADBE"],
    "GOOGL": ["META", "MSFT", "AMZN", "AAPL", "SNAP"],
    "META": ["GOOGL", "SNAP", "PINS", "MSFT", "AMZN"],
    "AAPL": ["MSFT", "GOOGL", "DELL", "HPQ", "SONY"],
    "TSLA": ["GM", "F", "TM", "RIVN", "NIO"],
    "NVDA": ["AMD", "INTC", "AVGO", "QCOM", "TSM"],
}


def _fallback_peers(state: AgentState) -> list[str]:
    ticker = state.ticker.upper()
    if ticker in PEER_FALLBACKS:
        return PEER_FALLBACKS[ticker]
    sector = (state.market.sector if state.market else "").lower()
    if "technology" in sector:
        return ["MSFT", "AAPL", "GOOGL", "ORCL", "ADBE"]
    if "consumer defensive" in sector or "consumer staples" in sector:
        return ["WMT", "COST", "TGT", "KR", "DG"]
    if "consumer cyclical" in sector or "retail" in sector:
        return ["AMZN", "WMT", "COST", "TGT", "EBAY"]
    if "communication" in sector:
        return ["GOOGL", "META", "NFLX", "DIS", "SNAP"]
    if "health" in sector:
        return ["JNJ", "PFE", "MRK", "ABBV", "LLY"]
    if "financial" in sector:
        return ["JPM", "BAC", "WFC", "GS", "MS"]
    return ["MSFT", "AAPL", "AMZN", "GOOGL", "JPM"]


def _suggest_peers(router: LLMRouter, state: AgentState) -> list[str]:
    system = load_prompt("peer_suggest").format(
        company=state.company_name, ticker=state.ticker
    )
    try:
        result = router.complete_json(
            "fast", system, "List the 3-5 peer tickers.", _Peers, max_tokens=80
        )
    except Exception as exc:
        log.warning("peer_suggest_failed_using_fallback", ticker=state.ticker, error=str(exc)[:200])
        return [t for t in _fallback_peers(state) if t != state.ticker.upper()][:5]
    cleaned = [t.strip().upper() for t in result.tickers if t.strip()]
    peers = [t for t in cleaned if t != state.ticker.upper()][:5]
    return peers or [t for t in _fallback_peers(state) if t != state.ticker.upper()][:5]


def _compute_scorecard(
    snapshot: MarketSnapshot | None,
    analysis: Any,  # FinancialAnalysis | None
) -> InvestmentScorecard:
    """Compute deterministic 1-5 investment scorecard from market + metrics data."""
    dimensions: list[ScorecardDimension] = []

    def _metric(metric_id: str) -> float | None:
        if analysis is None:
            return None
        m = analysis.metric_by_id(metric_id)
        return m.value if m else None

    # ── 1. Valuation (5=cheap, 1=expensive) ──
    fwd_pe = snapshot.forward_pe if snapshot else None
    pe_ttm = snapshot.pe_ttm if snapshot else None
    pe = fwd_pe or pe_ttm
    if pe is not None:
        if pe <= 0:
            score, note = 3, f"Negative/zero PE ({pe:.1f}x) — earnings not positive"
        elif pe < 15:
            score, note = 5, f"Forward P/E {pe:.1f}x — attractively valued"
        elif pe < 22:
            score, note = 4, f"Forward P/E {pe:.1f}x — fairly valued"
        elif pe < 30:
            score, note = 3, f"Forward P/E {pe:.1f}x — moderately priced"
        elif pe < 40:
            score, note = 2, f"Forward P/E {pe:.1f}x — premium valuation"
        else:
            score, note = 1, f"Forward P/E {pe:.1f}x — expensive relative to earnings"
        dimensions.append(ScorecardDimension(name="Valuation", score=score, rationale=note))

    # ── 2. Growth (5=fast grower, 1=declining) ──
    cagr = _metric("revenue_cagr_3y") or _metric("revenue_growth_yoy")
    if cagr is not None:
        if cagr > 25:
            score, note = 5, f"Revenue CAGR {cagr:.1f}% — high-growth company"
        elif cagr > 15:
            score, note = 4, f"Revenue CAGR {cagr:.1f}% — solid growth"
        elif cagr > 5:
            score, note = 3, f"Revenue CAGR {cagr:.1f}% — moderate growth"
        elif cagr >= 0:
            score, note = 2, f"Revenue CAGR {cagr:.1f}% — low growth"
        else:
            score, note = 1, f"Revenue CAGR {cagr:.1f}% — declining revenues"
        mid = "revenue_cagr_3y" if _metric("revenue_cagr_3y") is not None else "revenue_growth_yoy"
        dimensions.append(ScorecardDimension(name="Growth", score=score, rationale=note,
                                             metric_ids=[mid]))

    # ── 3. Profitability (5=highly profitable, 1=loss-making) ──
    fcf_margin = _metric("fcf_margin")
    net_margin_key: str | None = None
    if analysis is not None:
        net_margin_ids = sorted(
            (m.metric_id for m in analysis.metrics if m.metric_id.startswith("net_margin_fy")),
            reverse=True,
        )
        net_margin_key = net_margin_ids[0] if net_margin_ids else None
    nm = _metric(net_margin_key) if net_margin_key else None
    margin = fcf_margin if fcf_margin is not None else nm
    if margin is not None:
        if margin > 20:
            score, note = 5, f"FCF/net margin {margin:.1f}% — highly profitable"
        elif margin > 10:
            score, note = 4, f"FCF/net margin {margin:.1f}% — good profitability"
        elif margin > 3:
            score, note = 3, f"FCF/net margin {margin:.1f}% — moderate profitability"
        elif margin >= 0:
            score, note = 2, f"FCF/net margin {margin:.1f}% — thin margins"
        else:
            score, note = 1, f"FCF/net margin {margin:.1f}% — loss-making"
        mids = [k for k in ["fcf_margin", net_margin_key] if k]
        dimensions.append(ScorecardDimension(name="Profitability", score=score, rationale=note,
                                             metric_ids=mids))

    # ── 4. Financial Health (5=fortress balance sheet, 1=distressed) ──
    current_ratio = _metric("current_ratio")
    debt_ebitda = _metric("debt_to_ebitda")
    fcf_abs = _metric("fcf")
    if current_ratio is not None or debt_ebitda is not None:
        score = 3  # default
        parts = []
        if current_ratio is not None:
            if current_ratio >= 1.5:
                score = min(score + 1, 5)
                parts.append(f"current ratio {current_ratio:.2f}x (comfortable liquidity)")
            elif current_ratio < 1.0:
                score = max(score - 1, 1)
                parts.append(f"current ratio {current_ratio:.2f}x (<1, liquidity pressure)")
            else:
                parts.append(f"current ratio {current_ratio:.2f}x (adequate)")
        if debt_ebitda is not None:
            if debt_ebitda <= 1.0:
                score = min(score + 1, 5); parts.append(f"low leverage {debt_ebitda:.1f}x debt/EBITDA")
            elif debt_ebitda >= 4.0:
                score = max(score - 1, 1); parts.append(f"high leverage {debt_ebitda:.1f}x debt/EBITDA")
        else:
            parts.append("no debt/EBITDA computable (leverage credit unavailable)")
        if fcf_abs is not None and fcf_abs > 0:
            parts.append("positive free cash flow")
        # Rationale must always explain the score — unexplained scores are unauditable.
        note = "; ".join(parts) if parts else "Moderate balance sheet (no liquidity/leverage inputs)"
        mids = [m for m in ["current_ratio", "debt_to_ebitda"] if _metric(m) is not None]
        dimensions.append(ScorecardDimension(name="Financial Health", score=score,
                                             rationale=note, metric_ids=mids))

    # ── 5. Market Momentum (5=strong uptrend, 1=heavy distribution) ──
    if snapshot:
        change = snapshot.change_1y_pct
        short_pct = (snapshot.short_percent_float or 0) * 100  # convert to %
        if change is not None:
            if change > 30:
                score = 5
            elif change > 10:
                score = 4
            elif change > -5:
                score = 3
            elif change > -20:
                score = 2
            else:
                score = 1
            # Penalty for high short interest (>10% of float)
            if short_pct > 15:
                score = max(1, score - 1)
                note = (f"1Y return {change:+.1f}%; high short interest "
                        f"{short_pct:.1f}% of float (bearish signal)")
            elif short_pct > 10:
                note = f"1Y return {change:+.1f}%; elevated short interest {short_pct:.1f}%"
            else:
                note = f"1Y price return {change:+.1f}%"
            if change > 80:
                note += (
                    " — caveat: an extended move of this size is also a mean-reversion "
                    "risk, not purely a strength"
                )
            dimensions.append(ScorecardDimension(name="Market Momentum", score=score,
                                                 rationale=note))

    # ── 6. Earnings Quality ──
    accruals = _metric("accruals_ratio")
    cash_conv = _metric("cash_conversion")
    if accruals is not None or cash_conv is not None:
        score = 3
        parts = []
        if accruals is not None:
            if accruals < 2:
                score = min(score + 1, 5); parts.append(f"low accruals {accruals:.1f}%")
            elif accruals > 10:
                score = max(score - 1, 1); parts.append(f"high accruals {accruals:.1f}%")
        if cash_conv is not None and cash_conv > 0:
            if cash_conv >= 1.2:
                score = min(score + 1, 5); parts.append(f"cash conversion {cash_conv:.2f}x")
            elif cash_conv < 0.7:
                score = max(score - 1, 1); parts.append(f"low cash conv {cash_conv:.2f}x")
        note = "; ".join(parts) if parts else "Average earnings quality"
        mids = [m for m in ["accruals_ratio", "cash_conversion"] if _metric(m) is not None]
        dimensions.append(ScorecardDimension(name="Earnings Quality", score=score,
                                             rationale=note, metric_ids=mids))

    if not dimensions:
        return InvestmentScorecard(available=False)

    composite = sum(d.score for d in dimensions) / len(dimensions)
    if composite >= 4.5:
        label = "Strong Buy"
    elif composite >= 3.5:
        label = "Buy"
    elif composite >= 2.5:
        label = "Hold"
    elif composite >= 1.5:
        label = "Reduce"
    else:
        label = "Sell"

    return InvestmentScorecard(
        available=True,
        dimensions=dimensions,
        composite_score=round(composite, 2),
        composite_label=label,
    )


def market_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: build the market snapshot; degrade gracefully."""
    router = LLMRouter(state.run_id)
    updates: dict[str, Any] = {}
    data_gaps: list[str] = []

    log.info("market_node_start", ticker=state.ticker)
    try:
        log.info("market_snapshot_start", ticker=state.ticker)
        snapshot = get_snapshot(state.ticker)
        log.info("market_snapshot_end", ticker=state.ticker, available=snapshot.available)
        log.info("market_peers_start", ticker=state.ticker)
        snapshot.peers = get_peer_multiples(_suggest_peers(router, state))
        log.info("market_peers_end", ticker=state.ticker, peers=len(snapshot.peers))
        log.info("market_macro_start")
        snapshot.macro_notes = get_macro_notes()
        log.info("market_macro_end", notes=len(snapshot.macro_notes))

        try:
            log.info("market_summary_start", ticker=state.ticker)
            data = snapshot.model_dump(mode="json", exclude={"summary", "available", "error"})
            response = router.complete(
                "fast",
                load_prompt("market_summary"),
                json.dumps(data, default=str),
                max_tokens=200,
            )
            snapshot.summary = response.text.strip()
            log.info("market_summary_end", ticker=state.ticker, chars=len(snapshot.summary))
        except Exception as exc:
            log.warning("market_summary_failed", error=str(exc))

        updates["market"] = snapshot
    except Exception as exc:
        log.warning("market_data_unavailable", error=str(exc))
        snapshot = MarketSnapshot(available=False, error=str(exc), ticker=state.ticker)
        updates["market"] = snapshot
        data_gaps.append(f"Market data unavailable: {exc}")

    # Insider activity (best effort)
    try:
        log.info("insider_activity_start", ticker=state.ticker)
        insider = get_insider_activity(state.ticker)
        log.info("insider_activity_end", ticker=state.ticker, available=insider.available)
        updates["insider_activity"] = insider
        if not insider.available:
            data_gaps.append(f"Insider activity unavailable: {insider.error}")
    except Exception as exc:
        log.warning("insider_activity_skipped", error=str(exc))
        data_gaps.append(f"Insider activity fetch failed: {exc}")

    # Investment scorecard (computed after analysis is done — we'll recompute
    # in analyst_agent if analysis is not yet available, but compute here
    # with market data only for immediate use)
    log.info("scorecard_compute_start", ticker=state.ticker)
    scorecard = _compute_scorecard(
        snapshot if snapshot.available else None,
        state.analysis,  # may be None at this point; analyst_agent will recompute
    )
    updates["scorecard"] = scorecard
    log.info("scorecard_compute_end", ticker=state.ticker, available=scorecard.available)

    if data_gaps:
        updates["data_gaps"] = data_gaps
    log.info("market_node_end", ticker=state.ticker, data_gaps=len(data_gaps))
    return updates
