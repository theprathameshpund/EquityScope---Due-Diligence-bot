"""Final deterministic QA pass for assembled due diligence reports."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from app.report.schema import DDReport
from app.state import AgentState, Claim

_ERROR_PATTERNS = (
    re.compile(r"Error code:\s*\d+", re.I),
    re.compile(r"Traceback", re.I),
    re.compile(r"json_validate_failed", re.I),
    re.compile(r"invalid_request_error", re.I),
    re.compile(r"HTTP/\d", re.I),
    re.compile(r"\{['\"]error['\"]", re.I),
    re.compile(r"exception", re.I),
    re.compile(r"stack trace", re.I),
)
_GENERIC_THESIS_PATTERNS = (
    "data is sourced",
    "sec filings",
    "free-source",
    "methodology",
    "report identifies",
    "data unavailable",
    "unverifiable",
)
_DIRECTIONAL_RATINGS = {"Strong Buy", "Buy", "Hold", "Sell", "Strong Sell", "Reduce"}
_NEUTRAL_RATING = "Neutral / Insufficient Data"


def _clean_text(value: str) -> str:
    if any(pattern.search(value) for pattern in _ERROR_PATTERNS):
        return "Insufficient free-source data for this item."
    return value


def _clean_list(values: Iterable[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        item = _clean_text(value)
        if item not in cleaned:
            cleaned.append(item)
    return cleaned


def _has_core_valuation(report: DDReport) -> bool:
    valuation = report.valuation
    if any(
        value is not None and value > 0
        for value in (
            valuation.pe_ttm,
            valuation.forward_pe,
            valuation.ev_to_ebitda,
            valuation.price_to_sales,
        )
    ):
        return True
    return any(
        peer.pe_ttm is not None or peer.ev_to_ebitda is not None or peer.price_to_sales is not None
        for peer in valuation.peers
    )


def _score_from_formula(report: DDReport) -> int:
    if report.scorecard.available and report.scorecard.composite_score:
        base = report.scorecard.composite_score / 5.0 * 100.0
    else:
        base = 50.0
    risk_penalty = 8.0 * sum(1 for risk in report.risk_matrix if risk.severity == "high")
    red_flag_penalty = 4.0 * len(report.red_flags)
    valuation_penalty = 0.0 if _has_core_valuation(report) else 10.0
    return max(0, min(100, round(base - risk_penalty - red_flag_penalty - valuation_penalty)))


def _rating_from_score(score: int, report: DDReport) -> str:
    if not _has_core_valuation(report):
        return _NEUTRAL_RATING
    if score >= 82:
        return "Strong Buy"
    if score >= 65:
        return "Buy"
    if score >= 45:
        return "Hold"
    if score >= 25:
        return "Sell"
    return "Strong Sell"


def _is_substantive(item: str) -> bool:
    text = item.strip().lower()
    if len(text) < 28:
        return False
    return not any(pattern in text for pattern in _GENERIC_THESIS_PATTERNS)


def _dedupe_texts(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        key = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
        key = " ".join(key.split()[:18])
        if key and key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def _metric_year(period: str) -> int | None:
    matches = re.findall(r"(?:FY)?(20\d{2}|19\d{2})", period or "")
    if not matches:
        return None
    return max(int(year) for year in matches)


def _primary_metric_year(report: DDReport) -> int | None:
    years = [
        year
        for metric in report.financial_health.table.metrics
        for year in [_metric_year(metric.period)]
        if year is not None
    ]
    return max(years) if years else None


def _claim_uses_stale_metric(claim: Claim, report: DDReport, primary_year: int | None) -> bool:
    if primary_year is None or not claim.metric_ids:
        return False
    metric_by_id = {m.metric_id: m for m in report.financial_health.table.metrics}
    historical_context = any(
        phrase in claim.text.lower()
        for phrase in ("historical", "trend", "multi-year", "cagr", "over the period")
    )
    if historical_context:
        return False
    for metric_id in claim.metric_ids:
        metric = metric_by_id.get(metric_id)
        if metric is None:
            continue
        year = _metric_year(metric.period)
        if year is not None and primary_year - year > 2:
            return True
    return False


def _metric_sanity_reason(metric: Any) -> str | None:
    value = metric.value
    if value is None or not math.isfinite(value):
        return "missing, placeholder, or non-finite value"
    key = f"{metric.metric_id} {metric.name}".lower()
    if "revenue" in key and "growth" in key and abs(value) > 100:
        return "revenue growth exceeds 100% and requires re-verification"
    if "margin" in key and abs(value) > 90:
        return "margin exceeds 90% and requires re-verification"
    if "current_ratio" in key and not 0 <= value <= 5:
        return "current ratio falls outside the 0-5x sanity range"
    return None


def _remove_suspicious_metrics(report: DDReport) -> int:
    invalid: dict[str, str] = {}
    kept = []
    for metric in report.financial_health.table.metrics:
        reason = _metric_sanity_reason(metric)
        if reason is None:
            kept.append(metric)
        else:
            invalid[metric.metric_id] = reason
    if not invalid:
        return 0
    report.financial_health.table.metrics = kept
    removed_claims = 0
    for claim in list(report.all_claims()):
        if any(metric_id in invalid for metric_id in claim.metric_ids):
            if report.drop_claim(claim.claim_id):
                removed_claims += 1
    for metric_id, reason in invalid.items():
        report.data_gaps.append(
            f"Insufficient data for this item: metric {metric_id} was rejected because {reason}."
        )
    return len(invalid) + removed_claims


def _remove_stale_claims(report: DDReport) -> int:
    primary_year = _primary_metric_year(report)
    removed = 0
    for claim in list(report.all_claims()):
        if _claim_uses_stale_metric(claim, report, primary_year):
            if report.drop_claim(claim.claim_id):
                removed += 1
    return removed


def _remove_dropped_claim_references(report: DDReport) -> int:
    snippets: list[str] = []
    for gap in report.data_gaps:
        if "claim dropped" in gap.lower() or "unverifiable claim dropped" in gap.lower():
            _, _, tail = gap.partition(":")
            snippet = tail.strip().lower()[:80]
            if snippet:
                snippets.append(snippet)
    if not snippets:
        return 0

    removed = 0
    for claim in list(report.all_claims()):
        text = claim.text.lower()
        if any(snippet and snippet in text for snippet in snippets):
            if report.drop_claim(claim.claim_id):
                removed += 1

    def scrub_items(items: list[str]) -> list[str]:
        nonlocal removed
        kept = []
        for item in items:
            text = item.lower()
            if any(snippet and snippet in text for snippet in snippets):
                removed += 1
                continue
            kept.append(item)
        return kept

    report.institutional_summary.key_bull_thesis = scrub_items(report.institutional_summary.key_bull_thesis)
    report.institutional_summary.key_bear_thesis = scrub_items(report.institutional_summary.key_bear_thesis)
    report.institutional_summary.top_risks = scrub_items(report.institutional_summary.top_risks)
    report.investment_thesis.bull_case = scrub_items(report.investment_thesis.bull_case)
    report.investment_thesis.bear_case = scrub_items(report.investment_thesis.bear_case)
    report.investment_thesis.base_case = scrub_items(report.investment_thesis.base_case)
    return removed


def _scrub_raw_errors(report: DDReport) -> int:
    before = repr(report.model_dump(mode="json"))
    if report.valuation.dividend_yield is not None and not 0 <= report.valuation.dividend_yield <= 0.10:
        report.valuation.dividend_yield = None
        report.data_gaps.append("Insufficient data for this item: dividend yield failed the 0-10% sanity check.")
    if report.valuation.beta is not None and not -5 <= report.valuation.beta <= 5:
        report.valuation.beta = None
        report.data_gaps.append("Insufficient data for this item: beta failed the -5 to 5 sanity check.")
    if report.valuation.short_percent_float is not None and not 0 <= report.valuation.short_percent_float <= 1.0:
        report.valuation.short_percent_float = None
        report.data_gaps.append("Insufficient data for this item: short interest failed the 0-100% sanity check.")
    report.data_gaps = _clean_list(report.data_gaps)
    report.metadata.warnings = _clean_list(report.metadata.warnings)
    inst = report.institutional_summary
    inst.key_bull_thesis = _clean_list(inst.key_bull_thesis)
    inst.key_bear_thesis = _clean_list(inst.key_bear_thesis)
    inst.top_catalysts = _clean_list(inst.top_catalysts)
    inst.top_risks = _clean_list(inst.top_risks)
    inst.expected_return_range = _clean_text(inst.expected_return_range)
    thesis = report.investment_thesis
    thesis.bull_case = _clean_list(thesis.bull_case)
    thesis.base_case = _clean_list(thesis.base_case)
    thesis.bear_case = _clean_list(thesis.bear_case)
    thesis.probability_weighted_outcome = _clean_text(thesis.probability_weighted_outcome)
    thesis.monitoring_metrics = _clean_list(thesis.monitoring_metrics)
    thesis.upgrade_triggers = _clean_list(thesis.upgrade_triggers)
    thesis.downgrade_triggers = _clean_list(thesis.downgrade_triggers)
    thesis.exit_triggers = _clean_list(thesis.exit_triggers)
    report.management_questions = _clean_list(report.management_questions)
    for claim in report.all_claims():
        claim.text = _clean_text(claim.text)
    for risk in report.risk_matrix:
        risk.title = _clean_text(risk.title)
        risk.mitigation = _clean_text(risk.mitigation)
        risk.monitoring_metrics = _clean_list(risk.monitoring_metrics)
    after = repr(report.model_dump(mode="json"))
    return int(before != after)


def _repair_minimum_content(report: DDReport) -> int:
    changed = 0
    bull = _dedupe_texts(report.institutional_summary.key_bull_thesis)
    bear = _dedupe_texts(report.institutional_summary.key_bear_thesis)
    if sum(1 for item in bull if _is_substantive(item)) < 3:
        bull = ["Insufficient verified data to construct a balanced case."]
        changed += 1
    if sum(1 for item in bear if _is_substantive(item)) < 3:
        bear = ["Insufficient verified data to construct a balanced case."]
        changed += 1
    report.institutional_summary.key_bull_thesis = bull
    report.institutional_summary.key_bear_thesis = bear
    report.investment_thesis.bull_case = bull
    report.investment_thesis.bear_case = bear
    if len([risk for risk in report.risk_matrix if risk.claims or risk.mitigation]) < 3:
        report.data_gaps.append("Insufficient data for this section: Risk Matrix requires at least three verified risk entries.")
        changed += 1
    if len([q for q in report.management_questions if _is_substantive(q)]) < 3:
        report.management_questions = [
            "Insufficient verified data to construct management questions for this report."
        ]
        changed += 1
    return changed


def _dedupe_sections(report: DDReport) -> int:
    changed = 0
    before_bull = len(report.institutional_summary.key_bull_thesis)
    before_bear = len(report.institutional_summary.key_bear_thesis)
    before_base = len(report.investment_thesis.base_case)
    report.institutional_summary.key_bull_thesis = _dedupe_texts(report.institutional_summary.key_bull_thesis)
    report.institutional_summary.key_bear_thesis = _dedupe_texts(report.institutional_summary.key_bear_thesis)
    report.investment_thesis.base_case = _dedupe_texts(report.investment_thesis.base_case)
    changed += before_bull - len(report.institutional_summary.key_bull_thesis)
    changed += before_bear - len(report.institutional_summary.key_bear_thesis)
    changed += before_base - len(report.investment_thesis.base_case)
    executive_keys = {
        " ".join(re.sub(r"[^a-z0-9]+", " ", claim.text.lower()).split()[:18])
        for claim in report.executive_summary
    }
    report.investment_thesis.base_case = [
        item for item in report.investment_thesis.base_case
        if " ".join(re.sub(r"[^a-z0-9]+", " ", item.lower()).split()[:18]) not in executive_keys
    ]
    return changed


def _repair_rating_and_score(report: DDReport) -> int:
    changed = 0
    score = _score_from_formula(report)
    rating = _rating_from_score(score, report)
    old_score = report.quality_checks.final_scorecard.get("Overall Investment Score")
    if old_score != score:
        changed += 1
    report.quality_checks.final_scorecard["Overall Investment Score"] = score
    report.quality_checks.final_scorecard["Formula"] = (
        "Overall Investment Score = scorecard composite / 5 * 100 minus "
        "8 points per high-severity risk, 4 points per red flag, and 10 points "
        "when core valuation data is unavailable."
    )
    if report.institutional_summary.investment_rating != rating:
        changed += 1
        report.institutional_summary.investment_rating = rating
    if report.scorecard.available and report.scorecard.composite_label != rating:
        changed += 1
        report.scorecard.composite_label = rating
    report.investment_thesis.probability_weighted_outcome = (
        f"{report.institutional_summary.investment_rating} with "
        f"{report.institutional_summary.confidence_score}/100 confidence."
    )
    return changed


def _section_isolation(report: DDReport) -> int:
    changed = 0
    missing: list[tuple[str, bool, str]] = [
        ("Executive Summary", bool(report.executive_summary), "no verified executive-summary claims survived QA"),
        ("Business Overview", bool(report.business_overview), "no verified business-overview claims survived QA"),
        ("Financial Analysis", bool(report.financial_health.table.metrics or report.financial_health.commentary), "no current financial metrics or commentary available"),
        ("Valuation", _has_core_valuation(report), "core valuation multiples and peer benchmarks unavailable"),
        ("Risk Matrix", bool(report.risk_matrix), "no verified risk entries available"),
        ("Red Flags", bool(report.red_flags), "no verified red flags identified"),
        ("Recent Developments", bool(report.recent_developments), "no verified recent-development evidence available"),
        ("Insider Activity", bool(report.insider_activity.available and report.insider_activity.transactions), "insider transaction data unavailable"),
        ("Management Questions", bool(report.management_questions), "no management questions generated"),
    ]
    for section, ok, reason in missing:
        if ok:
            continue
        gap = f"Insufficient data for this section: {section} - {reason}."
        if gap not in report.data_gaps:
            report.data_gaps.append(gap)
            changed += 1
    return changed


def run_final_report_qa(report: DDReport, state: AgentState | None = None, logger: Any | None = None) -> list[dict[str, str]]:
    """Repair final report consistency and emit internal QA statuses."""
    statuses: list[dict[str, str]] = []

    def record(label: str, changed: int, reason: str) -> None:
        status = "fail" if changed else "pass"
        payload = {"check": label, "status": status, "reason": reason if changed else "ok"}
        statuses.append(payload)
        if logger is not None:
            logger.info("final_qa_status", **payload)

    section_changes = _section_isolation(report)
    record("SECTION ISOLATION", section_changes, f"repaired {section_changes} section disclosures")

    rating_changes = _repair_rating_and_score(report)
    record("RATING GATE", rating_changes, f"corrected rating/score labels in {rating_changes} places")

    # SCORE RECONCILIATION is logged separately even though the repair function also enforces rating consistency.
    score_after = _score_from_formula(report)
    score_displayed = report.quality_checks.final_scorecard.get("Overall Investment Score")
    score_changed = 0 if score_displayed == score_after else 1
    if score_changed:
        report.quality_checks.final_scorecard["Overall Investment Score"] = score_after
    record("SCORE RECONCILIATION", score_changed, "corrected displayed overall score from formula")

    sanity_removed = _remove_suspicious_metrics(report)
    record("DATA SANITY", sanity_removed, f"rejected {sanity_removed} suspicious metrics or dependent claims")

    stale_removed = _remove_stale_claims(report)
    record("RECENCY", stale_removed, f"removed {stale_removed} stale current-context claims")

    contradiction_removed = _remove_dropped_claim_references(report)
    record("CLAIM CONSISTENCY", contradiction_removed, f"removed {contradiction_removed} dropped-claim references")

    leak_changes = _scrub_raw_errors(report)
    record("NO RAW ERRORS", leak_changes, "scrubbed raw error text")

    duplicate_changes = _dedupe_sections(report)
    completeness_changes = _repair_minimum_content(report)
    record("MINIMUM CONTENT", completeness_changes, f"repaired {completeness_changes} minimum-content issues")

    # Final blocking QA checks requested for pipeline logs.
    math_changes = _repair_rating_and_score(report)
    record("CHECK 1 - Math", math_changes, f"recomputed score/rating in {math_changes} places")

    stale_removed_second = _remove_stale_claims(report)
    record("CHECK 2 - Recency", stale_removed_second, f"removed {stale_removed_second} stale current-context claims")

    contradiction_removed_second = _remove_dropped_claim_references(report)
    rating_consistent = (
        report.scorecard.composite_label == report.institutional_summary.investment_rating
        if report.scorecard.available and report.scorecard.composite_label
        else True
    )
    contradiction_changes = contradiction_removed_second + (0 if rating_consistent else 1)
    if not rating_consistent and report.scorecard.available:
        report.scorecard.composite_label = report.institutional_summary.investment_rating
    record("CHECK 3 - Contradiction", contradiction_changes, f"fixed {contradiction_changes} contradictions")

    leak_changes_second = _scrub_raw_errors(report)
    record("CHECK 4 - Leakage", leak_changes_second, "scrubbed raw error leakage")

    duplicate_changes_second = _dedupe_sections(report)
    record("CHECK 5 - Duplication", duplicate_changes + duplicate_changes_second, f"removed {duplicate_changes + duplicate_changes_second} duplicate bullets")

    completeness_changes_second = _repair_minimum_content(report)
    record("CHECK 6 - Completeness", completeness_changes_second, f"repaired {completeness_changes_second} completeness issues")

    _repair_rating_and_score(report)
    report.data_gaps = _clean_list(report.data_gaps)
    report.metadata.warnings = _clean_list(report.metadata.warnings)
    return statuses

