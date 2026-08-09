"""Regression: companies that switch XBRL tags must not lag a fiscal year.

Alphabet's FY2025 10-K moved revenue from
RevenueFromContractWithCustomerExcludingAssessedTax (data ends FY2024) to
Revenues (carries FY2025). First-alias-wins selection silently analyzed
year-old financials; aliases must be merged by fiscal year instead.
"""

from __future__ import annotations

from typing import Any

from app.tools.xbrl import parse_companyfacts


def _annual_item(fy: int, value: float, accn: str = "") -> dict[str, Any]:
    return {
        "start": f"{fy}-01-01",
        "end": f"{fy}-12-31",
        "val": value,
        "form": "10-K",
        "accn": accn or f"0001652044-{fy % 100:02d}-000010",
        "fy": fy + 1,
        "fp": "FY",
    }


def _concept(items: list[dict[str, Any]], label: str = "x") -> dict[str, Any]:
    return {"label": label, "units": {"USD": items}}


def test_alias_switch_keeps_history_and_latest_year() -> None:
    raw = {
        "cik": 1652044,
        "entityName": "Alphabet Inc.",
        "facts": {
            "us-gaap": {
                # Preferred alias: history through FY2024 only (old tag).
                "RevenueFromContractWithCustomerExcludingAssessedTax": _concept(
                    [_annual_item(fy, 100e9 + fy) for fy in (2021, 2022, 2023, 2024)]
                ),
                # Newer tag: carries FY2025 (plus overlapping years).
                "Revenues": _concept(
                    [_annual_item(fy, 200e9 + fy) for fy in (2023, 2024, 2025)]
                ),
                "NetIncomeLoss": _concept(
                    [_annual_item(fy, 50e9) for fy in (2023, 2024, 2025)]
                ),
            }
        },
    }
    facts = parse_companyfacts(raw)
    revenue_years = [v.fiscal_year for v in facts.facts["revenue"]]
    assert revenue_years == [2021, 2022, 2023, 2024, 2025]
    by_year = {v.fiscal_year: v.value for v in facts.facts["revenue"]}
    # Overlapping years keep the higher-priority alias's value...
    assert by_year[2024] == 100e9 + 2024
    # ...while the year only the newer tag carries is included.
    assert by_year[2025] == 200e9 + 2025


def test_single_alias_unchanged() -> None:
    raw = {
        "cik": 1,
        "entityName": "Simple Corp",
        "facts": {
            "us-gaap": {
                "Revenues": _concept([_annual_item(2024, 10e9), _annual_item(2025, 12e9)]),
            }
        },
    }
    facts = parse_companyfacts(raw)
    assert [v.fiscal_year for v in facts.facts["revenue"]] == [2024, 2025]
