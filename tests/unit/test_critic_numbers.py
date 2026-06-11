"""Numeric verification logic of the critic agent."""

from __future__ import annotations

from src.agents.critic_agent import _numbers_in, verify_numbers
from src.state import Claim, MetricValue


def _metric(metric_id: str, value: float, unit: str = "%") -> MetricValue:
    return MetricValue(
        metric_id=metric_id, name=metric_id, value=value, unit=unit,
        period="FY2024", inputs={"raw": value},
    )


def test_numbers_in_skips_years_and_small_counts() -> None:
    nums = _numbers_in("In 2024 we identified 3 risks and margin of 45.2")
    assert nums == [45.2]


def test_numbers_in_parses_thousands() -> None:
    assert _numbers_in("Revenue was 394,328 million") == [394328.0]


def test_matching_metric_value_passes() -> None:
    claim = Claim(text="Gross margin was 45.2% in FY2024.", metric_ids=["gm"])
    assert verify_numbers(claim, [_metric("gm", 45.2)], [])


def test_mismatched_number_fails() -> None:
    claim = Claim(text="Gross margin was 55.0% in FY2024.", metric_ids=["gm"])
    assert not verify_numbers(claim, [_metric("gm", 45.2)], [])


def test_scaled_billions_passes() -> None:
    claim = Claim(text="Free cash flow was 99.6 billion dollars.", metric_ids=["fcf"])
    metric = _metric("fcf", 99_600_000_000.0, unit="USD")
    assert verify_numbers(claim, [metric], [])


def test_rounded_value_passes() -> None:
    claim = Claim(text="Revenue grew about 10.0% last year.", metric_ids=["g"])
    assert verify_numbers(claim, [_metric("g", 10.04)], [])


def test_number_from_cited_chunk_passes() -> None:
    claim = Claim(text="The company employs 161,000 people.", citation_chunk_ids=["c1"])
    chunk = "As of fiscal year end we had approximately 161,000 full-time employees."
    assert verify_numbers(claim, [], [chunk])


def test_claim_without_numbers_passes() -> None:
    claim = Claim(text="The company faces intense competition.")
    assert verify_numbers(claim, [], [])
