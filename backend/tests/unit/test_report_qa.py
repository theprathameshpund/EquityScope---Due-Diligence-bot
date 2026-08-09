"""Regression tests for the final report QA pass and report credibility rules.

These lock in fixes that previously regressed between runs:
- confidence calibration (never 100, capped when claims are missing)
- bear-case supplementation instead of placeholder wipes
- basis-point metrics surviving the percentage sanity check
- insider-scale demotion of routine selling
- scenario-weighted outcome and grounded expected returns
- pipeline errors separated from data gaps
- the 0-verified-claims rating gate
"""

from __future__ import annotations

import pytest

from app.report.qa import run_final_report_qa, truncate_words
from app.report.render import render_markdown
from app.report.schema import (
    CompanyMeta,
    DDReport,
    EarningsQualitySection,
    FinancialHealthSection,
    InsiderActivitySection,
    InstitutionalExecutiveSummary,
    InvestmentThesisSection,
    MetricsTable,
    ReportMetadata,
    RiskEntry,
    ValuationSection,
)
from app.state import Claim, MetricValue


def _metric(metric_id: str, name: str, value: float, unit: str, period: str) -> MetricValue:
    return MetricValue(
        metric_id=metric_id, name=name, value=value, unit=unit, period=period,
        inputs={}, formula="test",
    )


def _base_report(**overrides: object) -> DDReport:
    defaults: dict = dict(
        company=CompanyMeta(name="Alphabet Inc.", ticker="GOOGL", cik="1652044"),
        executive_summary=[
            Claim(text="Revenue was strong.", metric_ids=["revenue"], section="executive_summary"),
        ],
        business_overview=[
            Claim(text="Search and ads business.", citation_chunk_ids=["chk_1"], section="business_overview"),
        ],
        financial_health=FinancialHealthSection(
            table=MetricsTable(metrics=[
                _metric("revenue", "Revenue FY2024", 350e9, "USD", "FY2024"),
                _metric("gross_margin_trend_bps", "Gross Margin YoY change", 157.5, "bps", "FY2023->FY2024"),
                _metric("net_margin_fy2024", "Net Margin FY2024", 28.6, "%", "FY2024"),
            ]),
        ),
        valuation=ValuationSection(
            price=359.91, market_cap=4.39e12, pe_ttm=27.5, forward_pe=24.7,
            ev_to_ebitda=26.9, target_low=340, target_mean=433, target_high=515,
            num_analysts=53,
        ),
        earnings_quality=EarningsQualitySection(
            accruals_ratio=-7.19, cash_conversion=1.25, quality_label="High",
        ),
        insider_activity=InsiderActivitySection(
            available=True, sentiment="bearish", net_shares=-250_000, net_value=-52_800_000,
        ),
        risk_matrix=[
            RiskEntry(title="Regulatory and Antitrust Pressure", severity="high", likelihood="medium",
                      claims=[Claim(text="Ongoing proceedings could harm results.",
                                    citation_chunk_ids=["chk_1"], section="risk_matrix")]),
            RiskEntry(title="Competitive and Technology Disruption", severity="medium", likelihood="medium",
                      mitigation="Mitigants: R&D investment defends the core franchise."),
            RiskEntry(title="Revenue Concentration and Cyclicality", severity="medium", likelihood="low",
                      mitigation="Mitigants: segment diversification dilutes concentration."),
        ],
        institutional_summary=InstitutionalExecutiveSummary(
            investment_rating="Buy", confidence_score=100,
            key_bull_thesis=[
                "Top-line momentum: revenue grew 13.9% in FY2024, supporting the franchise.",
                "Cash generation: free-cash-flow margin of 20.8% funds buybacks.",
            ],
            key_bear_thesis=[],
        ),
        investment_thesis=InvestmentThesisSection(
            probability_weighted_outcome="Buy with 100/100 confidence.",
        ),
        management_questions=["What drove the FY2024 operating-margin expansion and is it sustainable?"],
        data_gaps=[],
        metadata=ReportMetadata(run_id="run_test"),
    )
    defaults.update(overrides)
    return DDReport(**defaults)


def test_confidence_never_100_and_in_defensible_band() -> None:
    report = _base_report()
    run_final_report_qa(report)
    assert 20 <= report.institutional_summary.confidence_score <= 85


def test_bear_case_supplemented_not_wiped() -> None:
    report = _base_report()
    run_final_report_qa(report)
    bear = report.institutional_summary.key_bear_thesis
    assert len(bear) >= 3
    assert all("Insufficient verified data to construct" not in item for item in bear)


def test_bps_trend_metric_survives_sanity_check() -> None:
    report = _base_report()
    run_final_report_qa(report)
    ids = {m.metric_id for m in report.financial_health.table.metrics}
    assert "gross_margin_trend_bps" in ids


def test_percentage_margin_above_90_still_rejected() -> None:
    report = _base_report()
    report.financial_health.table.metrics.append(
        _metric("net_margin_fy2023", "Net Margin FY2023", 95.0, "%", "FY2023")
    )
    run_final_report_qa(report)
    ids = {m.metric_id for m in report.financial_health.table.metrics}
    assert "net_margin_fy2023" not in ids


def test_small_insider_selling_demoted_from_red_flags() -> None:
    report = _base_report(red_flags=[
        Claim(text="Bearish insider activity with net insider sales of $52.8 million.",
              citation_chunk_ids=["mkt_insiders"], section="red_flags"),
    ])
    run_final_report_qa(report)
    assert not report.red_flags
    assert any("% of market cap" in c.text for c in report.insider_activity.commentary)


def test_scenario_weighted_outcome_replaces_rating_confidence_text() -> None:
    report = _base_report()
    run_final_report_qa(report)
    outcome = report.investment_thesis.probability_weighted_outcome
    assert outcome.startswith("Probability-weighted 12-month return")
    assert "$433" in outcome or "433" in outcome


def test_unbacked_earnings_quality_label_downgraded() -> None:
    report = _base_report(earnings_quality=EarningsQualitySection(quality_label="High"))
    run_final_report_qa(report)
    assert report.earnings_quality.quality_label == "Not assessed"


def test_zero_verified_claims_suppresses_rating_and_caps_confidence() -> None:
    report = _base_report(
        executive_summary=[], business_overview=[], red_flags=[],
        risk_matrix=[
            RiskEntry(title="Regulatory Pressure", severity="medium", likelihood="medium",
                      mitigation="Mitigants: diversification."),
        ],
    )
    run_final_report_qa(report)
    assert report.institutional_summary.investment_rating == "Neutral / Insufficient Data"
    assert report.institutional_summary.confidence_score <= 35
    assert report.metadata.partial is True
    # The QA pass itself may add deterministic context claims (e.g. insider
    # scale); the gate triggers on anything below 3.
    assert report.quality_checks.final_scorecard["Verified claims"] < 3


def test_pipeline_errors_moved_out_of_data_gaps() -> None:
    report = _base_report(data_gaps=[
        "Report prose unavailable because the report writer could not produce validated structured output.",
        "Recent Developments: no material news items passed the relevance filter this run.",
    ])
    run_final_report_qa(report)
    assert all("report writer" not in gap.lower() for gap in report.data_gaps)
    assert any("report writer" in w.lower() for w in report.metadata.warnings)
    # Genuine data gap stays put.
    assert any("relevance filter" in gap for gap in report.data_gaps)


def test_score_key_labeled_and_reproducible() -> None:
    report = _base_report()
    run_final_report_qa(report)
    scorecard = report.quality_checks.final_scorecard
    assert "Overall Investment Score (0-100)" in scorecard
    assert "Overall Investment Score" not in scorecard  # legacy key removed


def test_truncate_words_never_cuts_mid_word() -> None:
    text = "Failure to detect and prevent an increase in problematic content could hurt our reputation"
    result = truncate_words(text, 50)
    assert result.endswith("…")
    body = result[:-1]
    assert text.startswith(body)
    assert text[len(body)] == " "  # cut happened exactly at a word boundary


def test_markdown_renders_partial_watermark() -> None:
    report = _base_report(metadata=ReportMetadata(run_id="run_test", partial=True))
    run_final_report_qa(report)
    md = render_markdown(report)
    assert "PARTIAL RUN" in md


def test_markdown_peer_table_has_subject_row_and_nm() -> None:
    from app.state import PeerMultiple

    report = _base_report()
    report.valuation.peers = [
        PeerMultiple(ticker="SNAP", pe_ttm=None, ev_to_ebitda=-38.2, price_to_sales=1.3, price_to_book=3.9),
    ]
    md = render_markdown(report)
    assert "GOOGL (this report)" in md
    assert "n.m." in md
    assert "-38.2" not in md


def test_claim_count_reconciled_between_prose_and_scorecard() -> None:
    report = _base_report()
    report.quality_checks.claim_verification = "23 claims verified by citations and/or deterministic metrics."
    run_final_report_qa(report)
    count = report.quality_checks.final_scorecard["Verified claims"]
    assert report.quality_checks.claim_verification.startswith(f"{count} claims verified")


def test_empty_red_flags_not_reported_as_data_gap() -> None:
    report = _base_report(red_flags=[])
    run_final_report_qa(report)
    assert not any("Red Flags" in gap for gap in report.data_gaps)


def test_executive_summary_topped_up_post_critic() -> None:
    # Simulates critic drops leaving the summary below the institutional floor.
    report = _base_report(executive_summary=[
        Claim(text="Revenue was strong.", metric_ids=["revenue"], section="executive_summary"),
    ])
    report.financial_health.table.metrics.extend([
        _metric("revenue_growth_yoy", "Revenue growth FY2024->FY2025", 15.09, "%", "FY2025"),
        _metric("fcf_margin", "FCF margin FY2025", 18.19, "%", "FY2025"),
        _metric("current_ratio", "Current ratio FY2025", 2.01, "x", "FY2025"),
    ])
    run_final_report_qa(report)
    assert len(report.executive_summary) >= 3


def test_coverage_ratio_definition_present_after_writer() -> None:
    # The writer adds the definition; QA must not strip it.
    report = _base_report()
    report.quality_checks.final_scorecard["Coverage Ratio definition"] = (
        "Share of 14 source-input and section checks that passed."
    )
    run_final_report_qa(report)
    assert "Coverage Ratio definition" in report.quality_checks.final_scorecard


@pytest.mark.parametrize("horizon_text", ["12-month"])
def test_expected_return_and_horizon_alignment(horizon_text: str) -> None:
    # The QA pass keeps the 12-month scenario outcome; the horizon label must
    # disclose the 12-month return basis (set by the writer, asserted here on
    # the schema default path via the outcome text).
    report = _base_report()
    run_final_report_qa(report)
    assert horizon_text in report.investment_thesis.probability_weighted_outcome
