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


def _write_commentary(
    router: LLMRouter, state: AgentState, analysis: FinancialAnalysis
) -> list[Claim]:
    payload = {
        "metrics": [m.model_dump() for m in analysis.metrics],
        "anomalies": analysis.anomalies,
    }
    system = load_prompt("analyst_commentary").format(company=state.company_name)
    try:
        result = router.complete_json(
            "smart", system, json.dumps(payload), _Commentary, max_tokens=1500
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


def analyst_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: compute financial analysis from EDGAR XBRL data.

    Data source priority:
      1. EDGAR XBRL companyfacts (us-gaap or ifrs-full taxonomy)
      2. Yahoo Finance financials (fallback for companies without XBRL on EDGAR)
    All numbers are deterministic; the LLM only writes commentary referencing
    pre-computed metric IDs.
    """
    data_gaps: list[str] = []
    facts = state.facts

    if facts is None:
        try:
            facts = fetch_financial_facts(state.cik)
        except Exception as exc:
            log.warning("xbrl_unavailable", cik=state.cik, error=str(exc))
            data_gaps.append(
                f"EDGAR XBRL data unavailable for {state.company_name} "
                f"(CIK {state.cik}): {exc}"
            )
            # Fallback: build from Yahoo Finance (works for ADRs / foreign issuers)
            facts = _facts_from_yfinance(state.ticker, state.cik)
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

    analysis = compute_metrics(facts)
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
        analysis.commentary = _write_commentary(router, state, analysis)
    except Exception as exc:
        log.warning("analyst_commentary_skipped", error=str(exc))

    log.info(
        "analysis_complete",
        metrics=len(analysis.metrics),
        anomalies=len(analysis.anomalies),
        commentary=len(analysis.commentary),
        source="xbrl" if not data_gaps else "yfinance",
    )
    updates = {"facts": facts, "analysis": analysis}
    if data_gaps:
        updates["data_gaps"] = data_gaps
    return updates
