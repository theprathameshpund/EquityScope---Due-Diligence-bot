"""Regression tests: fiscal-period anchoring and capex intensity in metrics."""

from __future__ import annotations

import re

from app.state import FactValue, FinancialFacts
from app.tools.metrics import compute_metrics


def _fact(concept: str, fy: int, value: float) -> FactValue:
    return FactValue(
        concept=concept,
        label=concept,
        unit="USD",
        value=value,
        end_date=f"{fy}-12-31",
        fiscal_year=fy,
        fiscal_period="FY",
        form="10-K",
        accession=f"0000000000-{fy % 100:02d}-000001",
    )


def _mixed_period_facts() -> FinancialFacts:
    """Revenue ends FY2024 while income/balance concepts reach FY2025 —
    the exact shape that previously produced silently mixed periods."""
    return FinancialFacts(
        cik="1652044",
        entity_name="Alphabet",
        facts={
            "revenue": [_fact("revenue", y, 200e9 + 30e9 * (y - 2020)) for y in range(2020, 2025)],
            "net_income": [_fact("net_income", y, 60e9 + 15e9 * (y - 2020)) for y in range(2020, 2026)],
            "operating_income": [_fact("op", y, 70e9 + 12e9 * (y - 2020)) for y in range(2020, 2026)],
            "operating_cash_flow": [_fact("ocf", y, 90e9 + 10e9 * (y - 2020)) for y in range(2020, 2026)],
            "capex": [_fact("capex", y, 25e9 + 5e9 * (y - 2020)) for y in range(2020, 2026)],
            "assets_current": [_fact("ac", y, 160e9) for y in range(2020, 2026)],
            "liabilities_current": [_fact("lc", y, 80e9) for y in range(2020, 2026)],
        },
    )


def test_headline_metrics_anchor_to_one_fiscal_year() -> None:
    analysis = compute_metrics(_mixed_period_facts())
    headline = {
        m.metric_id: m.period
        for m in analysis.metrics
        if re.fullmatch(r"FY\d{4}", m.period) and not re.search(r"_fy\d{4}$", m.metric_id)
    }
    assert headline, "expected headline metrics"
    assert set(headline.values()) == {"FY2024"}, f"mixed periods: {headline}"


def test_capex_intensity_metric_computed() -> None:
    analysis = compute_metrics(_mixed_period_facts())
    by_id = {m.metric_id: m for m in analysis.metrics}
    assert "capex_intensity" in by_id
    capex_metric = by_id["capex_intensity"]
    assert capex_metric.unit == "%"
    # FY2024: capex 45e9 / revenue 320e9 = 14.06%
    assert abs(capex_metric.value - 14.06) < 0.1


def test_rising_capex_intensity_flagged_as_anomaly() -> None:
    facts = _mixed_period_facts()
    # Spike FY2024 capex so intensity jumps well beyond the 2pp threshold.
    facts.facts["capex"] = [
        _fact("capex", y, 25e9 if y < 2024 else 80e9) for y in range(2020, 2026)
    ]
    analysis = compute_metrics(facts)
    assert any("Capex intensity rose" in a for a in analysis.anomalies)
