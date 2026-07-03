"""Regression: bear-case topic dedupe and metric-driven management questions."""

from __future__ import annotations

from app.agents.analyst_agent import _fallback_management_questions
from app.agents.writer_agent import _append_topic_unique
from app.state import AgentState, FinancialAnalysis, MetricValue, RunBudget


def _metric(metric_id: str, name: str, value: float, unit: str, period: str) -> MetricValue:
    return MetricValue(
        metric_id=metric_id, name=name, value=value, unit=unit, period=period,
        inputs={}, formula="test",
    )


def test_topic_dedupe_blocks_second_capex_item() -> None:
    bear: list[str] = []
    _append_topic_unique(
        bear,
        "Capex intensity rose to 22.7% of revenue in FY2025 (from 15.0% in FY2024) — "
        "forward FCF-margin pressure.",
    )
    _append_topic_unique(
        bear,
        "Capex intensity of 22.7% of revenue pressures forward free-cash-flow margins "
        "if returns on that investment lag.",
    )
    assert len(bear) == 1


def test_topic_dedupe_allows_distinct_topics() -> None:
    bear: list[str] = []
    _append_topic_unique(bear, "Capex intensity of 22.7% of revenue pressures FCF margins.")
    _append_topic_unique(bear, "Operating margin contracted -80 bps YoY - a margin-pressure signal.")
    assert len(bear) == 2


def _state_with_metrics() -> AgentState:
    return AgentState(
        run_id="run_q_test",
        company_input="GOOGL",
        ticker="GOOGL",
        cik="1652044",
        company_name="Alphabet Inc.",
        budget=RunBudget(token_limit=1000),
        analysis=FinancialAnalysis(
            metrics=[
                _metric("operating_margin_trend_bps", "Operating Margin YoY change", 469.0, "bps", "FY2023->FY2024"),
                _metric("capex_intensity", "Capex intensity FY2025", 22.7, "%", "FY2025"),
                _metric("fcf_margin", "FCF margin FY2025", 18.19, "%", "FY2025"),
                _metric("revenue_growth_yoy", "Revenue growth FY2024->FY2025", 15.09, "%", "FY2025"),
                _metric("share_dilution", "Diluted share count change", -5.41, "%", "FY2022-FY2025"),
            ],
            anomalies=[
                "Capex intensity rose to 22.7% of revenue in FY2025 (from 15.0% in FY2024) — forward FCF-margin pressure.",
            ],
        ),
    )


def test_fallback_questions_have_no_malformed_punctuation() -> None:
    state = _state_with_metrics()
    questions = _fallback_management_questions(state, state.analysis)
    assert questions
    for question in questions:
        assert not question.endswith(".?"), question
        assert "—" not in question.split("?")[0] or "anomaly" not in question.lower()


def test_fallback_questions_skip_capex_anomaly_duplicate() -> None:
    state = _state_with_metrics()
    questions = _fallback_management_questions(state, state.analysis)
    # The anomaly duplicate ("What explains ... Capex intensity rose ...") is
    # skipped because the dedicated capex/FCF question covers the topic.
    assert not any("what explains" in q.lower() and "capex" in q.lower() for q in questions)
    dedicated = [q for q in questions if "capex" in q.lower() and "22.7" in q]
    assert len(dedicated) == 1


def test_fallback_questions_are_metric_specific() -> None:
    state = _state_with_metrics()
    questions = _fallback_management_questions(state, state.analysis)
    joined = " ".join(questions)
    assert "469" in joined      # operating margin bps
    assert "15.1" in joined or "15.09" in joined  # revenue growth
