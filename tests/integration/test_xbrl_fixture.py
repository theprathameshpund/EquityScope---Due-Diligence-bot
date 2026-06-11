"""XBRL parsing against a recorded EDGAR companyfacts fixture."""

from __future__ import annotations

import json
from pathlib import Path

from src.tools.metrics import compute_metrics
from src.tools.xbrl import parse_companyfacts

FIXTURE = Path(__file__).parent / "fixtures" / "companyfacts_sample.json"


def test_parse_companyfacts_fixture() -> None:
    facts = parse_companyfacts(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert facts.entity_name == "Apple Inc."
    assert facts.cik == "320193"

    revenue = facts.facts["revenue"]
    assert [v.fiscal_year for v in revenue] == [2022, 2023, 2024]
    # The quarterly row tagged fp=FY must be excluded (span < 300 days).
    assert revenue[-1].value == 391035000000.0


def test_metrics_from_fixture() -> None:
    facts = parse_companyfacts(json.loads(FIXTURE.read_text(encoding="utf-8")))
    analysis = compute_metrics(facts)
    by_id = {m.metric_id: m for m in analysis.metrics}

    # 394328 → 391035 over 2 years: CAGR = -0.42%
    assert round(by_id["revenue_cagr_3y"].value, 2) == -0.42
    assert round(by_id["gross_margin_fy2024"].value, 2) == 46.21
    assert round(by_id["current_ratio"].value, 2) == 0.87
    # Raw XBRL inputs are carried for re-verification.
    assert by_id["gross_margin_fy2024"].inputs["numerator"] == 180683000000.0
