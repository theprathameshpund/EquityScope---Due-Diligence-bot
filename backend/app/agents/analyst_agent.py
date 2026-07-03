"""Analyst agent: deterministic XBRL metrics + LLM commentary that only
restates computed values (verified later by the critic)."""

from __future__ import annotations

import json
import math
from typing import Any

from pydantic import BaseModel, Field

from app.llm.router import LLMRouter, load_prompt
from app.logging_setup import get_logger
from app.state import (
    AgentState,
    Claim,
    FactValue,
    FinancialAnalysis,
    FinancialFacts,
    InvestmentScorecard,
    ScorecardDimension,
)
from app.tools.metrics import compute_metrics
from app.tools.xbrl import fetch_financial_facts

log = get_logger(__name__)


class _CommentaryClaim(BaseModel):
    text: str
    metric_ids: list[str] = Field(default_factory=list)


class _Commentary(BaseModel):
    claims: list[_CommentaryClaim]


def _facts_from_yfinance(ticker: str, cik: str) -> FinancialFacts | None:
    """Build FinancialFacts from yfinance when EDGAR XBRL is unavailable.

    Used as a fallback for foreign private issuers (e.g. 20-F filers) that
    don't have XBRL companyfacts on EDGAR. Data is sourced from Yahoo Finance
    and clearly labelled in the FactValue.form field as 'yfinance'.
    """
    try:
        import yfinance as yf  # already a project dependency

        yticker = yf.Ticker(ticker)
        info: dict[str, Any] = yticker.info or {}
        entity_name = str(info.get("longName") or info.get("shortName") or ticker)

        income = yticker.financials      # rows = line items, cols = fiscal year-end dates
        cashflow = yticker.cashflow
        balance = yticker.balance_sheet

        facts: dict[str, list[FactValue]] = {}

        # Maps: canonical concept → (DataFrame, row aliases to try)
        _MAPS = [
            ("revenue",            income,   ["Total Revenue", "Revenue"]),
            ("gross_profit",       income,   ["Gross Profit"]),
            ("operating_income",   income,   ["Operating Income", "Operating Income Loss"]),
            ("net_income",         income,   ["Net Income", "Net Income Common Stockholders"]),
            ("operating_cash_flow", cashflow, ["Operating Cash Flow", "Cash Flow From Operations"]),
            ("capex",              cashflow,  ["Capital Expenditure", "Capital Expenditures"]),
            ("assets_current",     balance,   ["Current Assets", "Total Current Assets"]),
            ("liabilities_current", balance,  ["Current Liabilities", "Total Current Liabilities"]),
            ("long_term_debt",     balance,   ["Long Term Debt"]),
        ]

        for canonical, df, row_aliases in _MAPS:
            if df is None or df.empty:
                continue
            for row_name in row_aliases:
                if row_name not in df.index:
                    continue
                row = df.loc[row_name]
                values: list[FactValue] = []
                for col in sorted(row.index):
                    try:
                        val = float(row[col])
                    except (TypeError, ValueError):
                        continue
                    if math.isnan(val):
                        continue
                    end_date = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)[:10]
                    values.append(
                        FactValue(
                            concept=row_name,
                            label=row_name,
                            unit="USD",
                            value=val,
                            end_date=end_date,
                            fiscal_year=int(end_date[:4]),
                            fiscal_period="FY",
                            form="yfinance",
                            accession="",
                        )
                    )
                if values:
                    facts[canonical] = sorted(values, key=lambda v: v.end_date)
                    break  # found this concept, move to next canonical

        if not facts:
            log.warning("yfinance_no_financials", ticker=ticker)
            return None

        log.info("yfinance_facts_extracted", ticker=ticker, concepts=sorted(facts.keys()))
        return FinancialFacts(cik=cik, entity_name=entity_name, facts=facts)

    except Exception as exc:
        log.warning("yfinance_facts_failed", ticker=ticker, error=str(exc))
        return None


class _CommentaryClaim(BaseModel):
    text: str
    metric_ids: list[str] = Field(default_factory=list)


class _Commentary(BaseModel):
    claims: list[_CommentaryClaim]


class _Questions(BaseModel):
    questions: list[str]


def _write_commentary(
    router: LLMRouter, state: AgentState, analysis: FinancialAnalysis
) -> list[Claim]:
    payload = {
        "metrics": [
            {"id": m.metric_id, "v": m.value, "u": m.unit, "p": m.period}
            for m in analysis.metrics
        ],
        "anomalies": analysis.anomalies,
    }
    system = load_prompt("analyst_commentary").format(company=state.company_name)
    try:
        result = router.complete_json(
            "fast", system, json.dumps(payload), _Commentary, max_tokens=600
        )
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


def _fallback_management_questions(state: AgentState, analysis: FinancialAnalysis) -> list[str]:
    """Metric-driven questions when the LLM is unavailable.

    Built from this run's own computed values so they are company-specific,
    never generic boilerplate that fits any ticker.
    """
    company = state.company_name or state.ticker
    by_id = {m.metric_id: m for m in analysis.metrics}
    questions: list[str] = []

    # Topics with a dedicated question below — skip their anomaly duplicates.
    dedicated_topics = ("capex intensity", "operating margin", "cash conversion")
    for anomaly in analysis.anomalies:
        if len(questions) >= 2:
            break
        if any(topic in anomaly.lower() for topic in dedicated_topics):
            continue
        # Rephrase as a clean question: strip the flag's trailing punctuation
        # and any appended "— consequence" tail so no ".?" artifacts leak.
        core = anomaly.split("—")[0].strip().rstrip(".;,")
        if core:
            questions.append(f"What explains this: {core}?")

    om_trend = by_id.get("operating_margin_trend_bps")
    if om_trend is not None:
        if om_trend.value > 0:
            questions.append(
                f"What drove the {om_trend.value:+,.0f} bps operating-margin expansion in "
                f"{om_trend.period.split('->')[-1]}, and how much of it is sustainable?"
            )
        else:
            questions.append(
                f"What is the plan to reverse the {abs(om_trend.value):,.0f} bps operating-margin "
                f"contraction in {om_trend.period.split('->')[-1]}?"
            )
    capex = by_id.get("capex_intensity")
    fcf_margin = by_id.get("fcf_margin")
    if capex is not None and fcf_margin is not None:
        questions.append(
            f"With capex at {capex.value:.1f}% of revenue, how will investment plans affect the "
            f"{fcf_margin.value:.1f}% free-cash-flow margin over the next 2-3 years?"
        )
    growth = by_id.get("revenue_growth_yoy")
    cagr = by_id.get("revenue_cagr_3y")
    if growth is not None:
        cagr_part = f" versus the {cagr.value:.1f}% 3-year CAGR" if cagr is not None else ""
        questions.append(
            f"Which segments and demand drivers underpin the {growth.value:.1f}% revenue growth in "
            f"{growth.period}{cagr_part}, and where is deceleration most likely?"
        )
    dilution = by_id.get("share_dilution")
    if dilution is not None and dilution.value < 0:
        questions.append(
            f"After reducing the diluted share count {abs(dilution.value):.1f}% over {dilution.period}, "
            "how is capital allocation prioritized among buybacks, dividends, capex, and M&A?"
        )
    cash_conv = by_id.get("cash_conversion")
    if cash_conv is not None and cash_conv.value < 1.0:
        questions.append(
            f"Why is cash conversion only {cash_conv.value:.2f}x, and when will operating cash flow "
            "catch up with reported earnings?"
        )
    questions.append(
        f"What legal, regulatory, antitrust, or litigation developments could materially change "
        f"{company}'s outlook, and what remedies or contingencies are being prepared?"
    )
    if state.data_gaps:
        questions.append(
            "Which of the publicly unavailable data fields noted in this report can management "
            "provide directly (segment detail, concentration, guidance)?"
        )
    return questions[:8]


def _generate_management_questions(
    router: LLMRouter, state: AgentState, analysis: FinancialAnalysis
) -> list[str]:
    """Generate 5-8 probing management questions from red flags + data gaps."""
    payload = {
        "company": state.company_name,
        "ticker": state.ticker,
        "anomalies": analysis.anomalies,
        "data_gaps": state.data_gaps,
        "metrics_summary": [
            {"id": m.metric_id, "name": m.name, "value": m.value, "unit": m.unit,
             "period": m.period}
            # Latest periods first so questions anchor to current-year trends.
            for m in sorted(analysis.metrics, key=lambda m: m.period, reverse=True)[:15]
        ],
    }
    system = load_prompt("management_questions").format(company=state.company_name)
    try:
        result = router.complete_json(
            "fast", system, json.dumps(payload, default=str), _Questions, max_tokens=400
        )
        return [q.strip() for q in result.questions if q.strip()][:8]
    except Exception as exc:
        log.warning("management_questions_failed_using_fallback", error=str(exc)[:200])
        return _fallback_management_questions(state, analysis)


def _recompute_scorecard(analysis: FinancialAnalysis, state: AgentState) -> InvestmentScorecard:
    """Recompute the scorecard now that full metrics are available.

    The market_agent computes a partial scorecard from market data only.
    After analyst_agent runs, we redo it with financial metrics included.
    Import here to avoid a circular import at module level.
    """
    from app.agents.market_agent import _compute_scorecard
    return _compute_scorecard(state.market, analysis)


def analyst_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: compute financial analysis from EDGAR XBRL data.

    Data source priority:
      1. EDGAR XBRL companyfacts (us-gaap or ifrs-full taxonomy)
      2. Yahoo Finance financials (fallback for companies without XBRL on EDGAR)
    All numbers are deterministic; the LLM only writes commentary referencing
    pre-computed metric IDs.
    """
    log.info("analyst_node_start", ticker=state.ticker, cik=state.cik)
    data_gaps: list[str] = []
    facts = state.facts

    if facts is None:
        try:
            log.info("financial_facts_fetch_start", cik=state.cik)
            facts = fetch_financial_facts(state.cik)
            log.info("financial_facts_fetch_end", cik=state.cik)
        except Exception as exc:
            log.warning("xbrl_unavailable", cik=state.cik, error=str(exc))
            data_gaps.append(
                f"EDGAR XBRL data unavailable for {state.company_name} "
                f"(CIK {state.cik}): {exc}"
            )
            # Fallback: build from Yahoo Finance (works for ADRs / foreign issuers)
            log.info("yfinance_fallback_start", ticker=state.ticker)
            facts = _facts_from_yfinance(state.ticker, state.cik)
            log.info("yfinance_fallback_end", ticker=state.ticker, available=facts is not None)
            if facts is None:
                data_gaps.append(
                    f"Yahoo Finance financial fallback also returned no data for {state.ticker}. "
                    "Financial metrics section will be empty."
                )
                return {"analysis": FinancialAnalysis(), "data_gaps": data_gaps}
            data_gaps.append(
                f"Financial metrics for {state.company_name} are sourced from Yahoo Finance "
                f"(ticker: {state.ticker}) because EDGAR XBRL data was unavailable. "
                "Source: https://finance.yahoo.com/quote/" + state.ticker
            )

    log.info("financial_metrics_compute_start", ticker=state.ticker)
    analysis = compute_metrics(facts)
    log.info("financial_metrics_compute_end", ticker=state.ticker, metrics=len(analysis.metrics), anomalies=len(analysis.anomalies))
    if not analysis.metrics:
        data_gaps.append(
            "No computable financial metrics found. "
            "The filing data may lack the required line items (revenue, net income, etc.)."
        )
        updates: dict[str, Any] = {"facts": facts, "analysis": analysis}
        if data_gaps:
            updates["data_gaps"] = data_gaps
        return updates

    router = LLMRouter(state.run_id)
    try:
        log.info("analyst_commentary_start", ticker=state.ticker)
        analysis.commentary = _write_commentary(router, state, analysis)
        log.info("analyst_commentary_end", ticker=state.ticker, chars=len(analysis.commentary))
    except Exception as exc:
        log.warning("analyst_commentary_skipped", error=str(exc))

    # Recompute scorecard with full financial metrics now available.
    try:
        log.info("analyst_scorecard_start", ticker=state.ticker)
        scorecard = _recompute_scorecard(analysis, state)
        log.info("analyst_scorecard_end", ticker=state.ticker, available=scorecard.available)
    except Exception as exc:
        log.warning("scorecard_recompute_failed", error=str(exc))
        scorecard = state.scorecard  # keep the market-only version

    # Generate management questions from anomalies + data gaps.
    management_questions: list[str] = []
    try:
        log.info("management_questions_start", ticker=state.ticker)
        management_questions = _generate_management_questions(router, state, analysis)
        log.info("management_questions_end", ticker=state.ticker, questions=len(management_questions))
    except Exception as exc:
        log.warning("management_questions_skipped", error=str(exc))

    log.info(
        "analysis_complete",
        metrics=len(analysis.metrics),
        anomalies=len(analysis.anomalies),
        commentary=len(analysis.commentary),
        questions=len(management_questions),
        source="xbrl" if not data_gaps else "yfinance",
    )
    updates = {
        "facts": facts,
        "analysis": analysis,
        "scorecard": scorecard,
        "management_questions": management_questions,
    }
    if data_gaps:
        updates["data_gaps"] = data_gaps
    return updates
