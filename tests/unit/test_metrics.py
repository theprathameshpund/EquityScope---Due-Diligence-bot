"""Deterministic metric math tests."""

from __future__ import annotations

import pytest
from src.state import FactValue, FinancialFacts
from src.tools.metrics import cagr, compute_metrics, pct_margin


def _fact(concept: str, fy: int, value: float, unit: str = "USD") -> FactValue:
    return FactValue(
        concept=concept,
        label=concept,
        unit=unit,
        value=value,
        end_date=f"{fy}-09-30",
        start_date=f"{fy - 1}-10-01",
        fiscal_year=fy,
        fiscal_period="FY",
        form="10-K",
        accession=f"0000000000-{fy % 100:02d}-000001",
    )


@pytest.fixture
def facts() -> FinancialFacts:
    return FinancialFacts(
        cik="320193",
        entity_name="Test Corp",
        facts={
            "revenue": [_fact("Revenues", fy, v) for fy, v in
                        [(2021, 100.0), (2022, 110.0), (2023, 121.0), (2024, 133.1)]],
            "gross_profit": [_fact("GrossProfit", fy, v) for fy, v in
                             [(2023, 60.5), (2024, 59.895)]],
            "operating_income": [_fact("OperatingIncomeLoss", fy, v) for fy, v in
                                 [(2023, 30.25), (2024, 26.62)]],
            "net_income": [_fact("NetIncomeLoss", fy, v) for fy, v in
                           [(2023, 24.2), (2024, 26.62)]],
            "depreciation_amortization": [_fact("DepreciationAndAmortization", 2024, 10.0)],
            "long_term_debt": [_fact("LongTermDebtNoncurrent", 2024, 100.0)],
            "long_term_debt_current": [_fact("LongTermDebtCurrent", 2024, 10.0)],
            "assets_current": [_fact("AssetsCurrent", 2024, 50.0)],
            "liabilities_current": [_fact("LiabilitiesCurrent", 2024, 100.0)],
            "operating_cash_flow": [
                _fact("NetCashProvidedByUsedInOperatingActivities", 2024, 40.0)
            ],
            "capex": [_fact("PaymentsToAcquirePropertyPlantAndEquipment", 2024, 10.0)],
            "diluted_shares": [
                _fact("WeightedAverageNumberOfDilutedSharesOutstanding", fy, v, "shares")
                for fy, v in [(2021, 1000.0), (2024, 1300.0)]
            ],
        },
    )


def test_cagr_exact() -> None:
    # 100 → 133.1 over 3 years is exactly 10%/yr
    assert cagr(100.0, 133.1, 3) == pytest.approx(10.0, abs=1e-9)


def test_cagr_undefined_for_nonpositive() -> None:
    assert cagr(0.0, 100.0, 3) is None
    assert cagr(100.0, -5.0, 3) is None


def test_pct_margin() -> None:
    assert pct_margin(45.0, 100.0) == pytest.approx(45.0)
    assert pct_margin(45.0, 0.0) is None


def test_revenue_metrics(facts: FinancialFacts) -> None:
    analysis = compute_metrics(facts)
    by_id = {m.metric_id: m for m in analysis.metrics}

    assert by_id["revenue_cagr_3y"].value == pytest.approx(10.0, abs=0.01)
    assert by_id["revenue_growth_yoy"].value == pytest.approx(10.0, abs=0.01)
    # Inputs carry the raw XBRL values used.
    assert by_id["revenue_cagr_3y"].inputs["revenue_fy2021"] == 100.0
    assert by_id["revenue_cagr_3y"].inputs["revenue_fy2024"] == 133.1


def test_margins_and_trend_flag(facts: FinancialFacts) -> None:
    analysis = compute_metrics(facts)
    by_id = {m.metric_id: m for m in analysis.metrics}

    assert by_id["gross_margin_fy2023"].value == pytest.approx(50.0, abs=0.01)
    assert by_id["gross_margin_fy2024"].value == pytest.approx(45.0, abs=0.01)
    # 50% → 45% = -500bps, beyond the 300bps anomaly threshold.
    assert by_id["gross_margin_trend_bps"].value == pytest.approx(-500.0, abs=1.0)
    assert any("Gross Margin" in a for a in analysis.anomalies)


def test_leverage_metrics(facts: FinancialFacts) -> None:
    analysis = compute_metrics(facts)
    by_id = {m.metric_id: m for m in analysis.metrics}

    # (100 + 10) / (26.62 + 10) = 3.0038...
    assert by_id["debt_to_ebitda"].value == pytest.approx(3.0, abs=0.01)
    assert by_id["current_ratio"].value == pytest.approx(0.5, abs=0.001)
    assert any("Current ratio" in a for a in analysis.anomalies)
    assert any("Debt/EBITDA" in a for a in analysis.anomalies)


def test_fcf_metrics(facts: FinancialFacts) -> None:
    analysis = compute_metrics(facts)
    by_id = {m.metric_id: m for m in analysis.metrics}

    assert by_id["fcf"].value == pytest.approx(30.0)
    assert by_id["fcf_margin"].value == pytest.approx(30.0 / 133.1 * 100, abs=0.01)


def test_dilution_flag(facts: FinancialFacts) -> None:
    analysis = compute_metrics(facts)
    by_id = {m.metric_id: m for m in analysis.metrics}

    assert by_id["share_dilution"].value == pytest.approx(30.0, abs=0.01)
    assert any("dilution" in a.lower() for a in analysis.anomalies)


def test_empty_facts_no_crash() -> None:
    analysis = compute_metrics(FinancialFacts(cik="1", entity_name="Empty", facts={}))
    assert analysis.metrics == []
    assert analysis.anomalies == []
