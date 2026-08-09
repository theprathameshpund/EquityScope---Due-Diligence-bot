"""EDGAR XBRL companyfacts JSON → typed FinancialFacts.

Numbers here are taken verbatim from EDGAR; all derived metrics are
computed in src/tools/metrics.py. The LLM never sees raw JSON and never
does arithmetic.
"""

from __future__ import annotations

from typing import Any

from app.logging_setup import get_logger
from app.state import FactValue, FinancialFacts
from app.tools.edgar import EdgarClient

log = get_logger(__name__)

# Annual report form types accepted for XBRL data extraction.
_ANNUAL_FORMS = {"10-K", "20-F", "20-F/A"}

# Concepts we extract, with fallbacks tried in order (us-gaap taxonomy).
CONCEPT_ALIASES: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "depreciation_amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
    ],
    "assets_current": ["AssetsCurrent"],
    "liabilities_current": ["LiabilitiesCurrent"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "long_term_debt_current": ["LongTermDebtCurrent"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "diluted_shares": [
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasic",
    ],
}

_PREFERRED_UNITS = ("USD", "shares")

# IFRS-full taxonomy equivalents for foreign private issuers (20-F filers).
IFRS_CONCEPT_ALIASES: dict[str, list[str]] = {
    "revenue": [
        "Revenue",
        "RevenueFromContractsWithCustomers",
        "RevenueFromRenderingOfServicesAndSaleOfGoods",
    ],
    "gross_profit": ["GrossProfit"],
    "operating_income": [
        "ProfitLossFromOperatingActivities",
        "OperatingProfitLoss",
    ],
    "net_income": [
        "ProfitLoss",
        "ProfitLossAttributableToOwnersOfParent",
        "ComprehensiveIncome",
    ],
    "assets_current": ["CurrentAssets"],
    "liabilities_current": ["CurrentLiabilities"],
    "long_term_debt": [
        "NoncurrentPortionOfLongtermBorrowings",
        "LongtermBorrowings",
        "Borrowings",
    ],
    "operating_cash_flow": ["CashFlowsFromUsedInOperatingActivities"],
    "capex": [
        "PurchaseOfPropertyPlantAndEquipment",
        "AcquisitionOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    ],
    "diluted_shares": ["WeightedAverageShares"],
}


def _annual_values(concept_data: dict[str, Any], concept: str) -> list[FactValue]:
    """Extract one value per fiscal year from 10-K facts.

    NOTE: EDGAR's `fy`/`fp` describe the FILING's fiscal period, not the data
    period — a FY2025 10-K reports FY2023/FY2024 comparatives also tagged
    fy=2025. We therefore key annual values by the period END DATE and label
    the fiscal year from it; the newest filing of a duplicate period wins.
    """
    from datetime import date

    units: dict[str, list[dict[str, Any]]] = concept_data.get("units", {})
    unit_name = next((u for u in _PREFERRED_UNITS if u in units), None)
    if unit_name is None:
        return []

    by_end: dict[str, dict[str, Any]] = {}
    for item in units[unit_name]:
        if item.get("form") not in _ANNUAL_FORMS or item.get("val") is None:
            continue
        end = item.get("end")
        if not end:
            continue
        start = item.get("start")
        if start:
            # Duration facts must span roughly a full year (excludes the
            # quarterly comparatives that 10-Ks also contain).
            span = (date.fromisoformat(str(end)) - date.fromisoformat(str(start))).days
            if span < 300:
                continue
        existing = by_end.get(str(end))
        if existing is None or str(item.get("accn", "")) > str(existing.get("accn", "")):
            by_end[str(end)] = item

    values: list[FactValue] = []
    for end in sorted(by_end):
        item = by_end[end]
        fiscal_year = int(end[:4])
        values.append(
            FactValue(
                concept=concept,
                label=str(concept_data.get("label") or concept),
                unit=unit_name,
                value=float(item["val"]),
                end_date=end,
                start_date=str(item["start"]) if item.get("start") else None,
                fiscal_year=fiscal_year,
                fiscal_period="FY",
                form=str(item.get("form", "")),
                accession=str(item.get("accn", "")),
            )
        )
    return values


def parse_companyfacts(raw: dict[str, Any]) -> FinancialFacts:
    """Convert an EDGAR companyfacts payload into typed FinancialFacts.

    Tries US-GAAP taxonomy first (domestic 10-K filers), then IFRS-full
    taxonomy (foreign private issuers filing 20-F with IFRS data).
    """
    facts_data: dict[str, Any] = raw.get("facts", {})
    gaap: dict[str, Any] = facts_data.get("us-gaap", {})
    ifrs: dict[str, Any] = facts_data.get("ifrs-full", {})

    facts: dict[str, list[FactValue]] = {}

    def _merged_alias_values(taxonomy: dict[str, Any], aliases: list[str]) -> list[FactValue]:
        """Merge every alias by fiscal year, earlier-listed alias winning ties.

        Companies switch tags between filings (e.g. Alphabet's revenue moved
        from RevenueFromContractWithCustomerExcludingAssessedTax to Revenues
        in its FY2025 10-K). Taking only the first alias with data silently
        lagged the analysis by a full fiscal year — merging keeps the full
        history AND the newest year regardless of which tag carries it.
        """
        merged: dict[int, FactValue] = {}
        for alias in aliases:
            if alias not in taxonomy:
                continue
            for value in _annual_values(taxonomy[alias], alias):
                merged.setdefault(value.fiscal_year, value)
        return sorted(merged.values(), key=lambda v: v.fiscal_year)

    # Pass 1: US-GAAP concepts
    for canonical, aliases in CONCEPT_ALIASES.items():
        values = _merged_alias_values(gaap, aliases)
        if values:
            facts[canonical] = values

    # Pass 2: IFRS-full concepts for any concept still missing (foreign issuers)
    if ifrs:
        for canonical, aliases in IFRS_CONCEPT_ALIASES.items():
            if canonical in facts:
                continue  # already populated from US-GAAP
            values = _merged_alias_values(ifrs, aliases)
            if values:
                facts[canonical] = values

    log.info(
        "companyfacts_parsed",
        cik=str(raw.get("cik", "")),
        taxonomy="us-gaap" if gaap else ("ifrs-full" if ifrs else "none"),
        concepts_found=sorted(facts.keys()),
    )
    return FinancialFacts(
        cik=str(raw.get("cik", "")),
        entity_name=str(raw.get("entityName", "")),
        facts=facts,
    )


def fetch_financial_facts(cik: str, edgar: EdgarClient | None = None) -> FinancialFacts:
    """Download (cached) and parse companyfacts for a CIK."""
    client = edgar or EdgarClient()
    raw = client.get_companyfacts(cik)
    parsed = parse_companyfacts(raw)
    log.info(
        "companyfacts_parsed",
        cik=cik,
        concepts=sorted(parsed.facts.keys()),
    )
    return parsed
