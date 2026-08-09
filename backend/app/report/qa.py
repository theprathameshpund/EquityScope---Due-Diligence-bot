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


def truncate_words(text: str, max_chars: int = 140) -> str:
    """Truncate at a word boundary with an ellipsis — never mid-word."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


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
    unit = str(getattr(metric, "unit", "")).lower()
    if "revenue" in key and "growth" in key and unit == "%" and abs(value) > 100:
        return "revenue growth exceeds 100% and requires re-verification"
    # Only percentage-level margins are bounded at 90; basis-point trend
    # metrics (unit "bps", e.g. +157 bps YoY) are deltas and legitimately
    # exceed 90 — validating them as percentages silently destroyed valid data.
    if "margin" in key and unit == "%" and "trend" not in key and abs(value) > 90:
        return "margin exceeds 90% and requires re-verification"
    if "margin_trend" in key and unit == "bps" and abs(value) > 3000:
        return "margin swing exceeds 3000 bps YoY and requires re-verification"
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
    for gap in [*report.data_gaps, *report.metadata.warnings]:
        if "claim dropped" in gap.lower() or "unverifiable claim dropped" in gap.lower():
            _, _, tail = gap.partition(":")
            snippet = tail.strip().lower().rstrip("…")[:80]
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


def _supplement_bear_case(report: DDReport) -> list[str]:
    """Deterministic bear-case candidates derived from the report's own contents.

    A DD report must never ship without a bear side while risks, red flags, or
    stretched valuation data exist elsewhere in the same report.
    """
    candidates: list[str] = []
    for risk in report.risk_matrix:
        detail = (
            risk.claims[0].text if risk.claims
            else (risk.mitigation if _is_substantive(risk.mitigation) else "")
        )
        entry = f"{risk.title} ({risk.severity} severity)"
        candidates.append(f"{entry}: {detail}" if detail else f"{entry} - see Risk Matrix.")
    for flag in report.red_flags:
        candidates.append(flag.text)
    for eq_flag in report.earnings_quality.flags:
        candidates.append(eq_flag)
    if report.valuation.forward_pe is not None and report.valuation.forward_pe > 25:
        candidates.append(
            f"Valuation embeds high expectations (forward P/E {report.valuation.forward_pe:.1f}x); "
            "multiple compression is a downside risk if growth or margins disappoint."
        )
    return candidates


def _top_up_executive_summary(report: DDReport) -> int:
    """Post-critic top-up: if verification drops left the executive summary
    below 3 claims, rebuild from the deterministic metrics table (metric-cited
    claims need no NLI verification)."""
    if len(report.executive_summary) >= 3:
        return 0
    from app.state import Claim as _Claim

    cited = {mid for claim in report.executive_summary for mid in claim.metric_ids}
    added = 0
    metrics = {m.metric_id: m for m in report.financial_health.table.metrics}
    templates: list[tuple[str, str]] = [
        ("revenue_growth_yoy", "Revenue {dir} {absval:.2f}% in {period}."),
        ("operating_margin_trend_bps", "Operating margin {dir2} {absval:,.0f} bps YoY ({period})."),
        ("fcf_margin", "Free-cash-flow margin was {val:.2f}% in {period}."),
        ("current_ratio", "Current ratio was {val:.2f}x in {period}."),
        ("cash_conversion", "Cash conversion was {val:.2f}x in {period}."),
    ]
    for metric_id, template in templates:
        if len(report.executive_summary) >= 3:
            break
        metric = metrics.get(metric_id)
        if metric is None or metric.metric_id in cited:
            continue
        text = template.format(
            val=metric.value,
            absval=abs(metric.value),
            period=metric.period,
            dir="grew" if metric.value >= 0 else "declined",
            dir2="expanded" if metric.value >= 0 else "contracted",
        )
        report.executive_summary.append(
            _Claim(text=text, metric_ids=[metric.metric_id], section="executive_summary")
        )
        cited.add(metric.metric_id)
        added += 1
    return added


def _repair_minimum_content(report: DDReport) -> int:
    """Top up thin thesis lists from report evidence — never wipe them.

    The old behavior replaced the whole bull/bear list with a single
    'insufficient data' placeholder, which destroyed valid content and
    contradicted the rest of the report.
    """
    changed = _top_up_executive_summary(report)
    bull = _dedupe_texts(report.institutional_summary.key_bull_thesis)
    bear = _dedupe_texts(report.institutional_summary.key_bear_thesis)

    # A DD report must argue both sides: minimum 3 bear points, symmetric with bull.
    if sum(1 for item in bear if _is_substantive(item)) < 3:
        for candidate in _supplement_bear_case(report):
            if sum(1 for item in bear if _is_substantive(item)) >= 3:
                break
            if candidate not in bear:
                bear.append(candidate)
                changed += 1
    if sum(1 for item in bear if _is_substantive(item)) < 3:
        note = "Limited verified bear-case evidence this run - see Risk Matrix and Data Gaps."
        if note not in bear:
            bear.append(note)
            changed += 1
    if sum(1 for item in bull if _is_substantive(item)) < 2:
        note = "Limited verified bull-case evidence this run - see Financial Health metrics."
        if note not in bull:
            bull.append(note)
            changed += 1

    report.institutional_summary.key_bull_thesis = bull[:5]
    report.institutional_summary.key_bear_thesis = bear[:5]
    report.investment_thesis.bull_case = bull[:5]
    report.investment_thesis.bear_case = bear[:5]
    if len([risk for risk in report.risk_matrix if risk.claims or risk.mitigation]) < 3:
        gap = "Insufficient data for this section: Risk Matrix requires at least three verified risk entries."
        if gap not in report.data_gaps:
            report.data_gaps.append(gap)
            changed += 1
    if not report.management_questions:
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


def _recalibrate_confidence(report: DDReport) -> int:
    """Confidence from final evidence coverage and thesis balance — never 100.

    30-75 base from section coverage, penalized when the bear case is thin,
    the risk matrix is short, core valuation is missing, or data gaps exist.
    Capped at 85: a free-source automated report never warrants full certainty.
    """
    bull = [i for i in report.institutional_summary.key_bull_thesis if _is_substantive(i)]
    bear = [i for i in report.institutional_summary.key_bear_thesis if _is_substantive(i)]
    eq = report.earnings_quality
    checks = [
        bool(report.executive_summary),
        bool(report.business_overview),
        bool(report.financial_health.table.metrics),
        _has_core_valuation(report),
        len(report.risk_matrix) >= 3,
        bool(report.recent_developments),
        bool(report.insider_activity.available),
        eq.accruals_ratio is not None or eq.cash_conversion is not None,
        len(bull) >= 2,
        len(bear) >= 2,
    ]
    coverage = sum(1 for check in checks if check) / len(checks)
    confidence = 30 + round(45 * coverage)
    if len(bear) < 2:
        confidence -= 10
    if len(report.risk_matrix) < 3:
        confidence -= 5
    if not _has_core_valuation(report):
        confidence -= 10
    confidence -= min(10, 2 * len(report.data_gaps))
    return max(20, min(85, confidence))


def _scenario_outcome_from_valuation(report: DDReport) -> str | None:
    """Scenario-weighted 12-month outcome from price + analyst targets, or None."""
    v = report.valuation
    if not (v.price and v.target_low and v.target_mean and v.target_high):
        return None
    bear = (v.target_low / v.price - 1.0) * 100.0
    base = (v.target_mean / v.price - 1.0) * 100.0
    bull = (v.target_high / v.price - 1.0) * 100.0
    weighted = 0.25 * bear + 0.50 * base + 0.25 * bull
    return (
        f"Probability-weighted 12-month return ~ {weighted:+.0f}% "
        f"(bear 25%: {bear:+.0f}% at ${v.target_low:,.0f} | "
        f"base 50%: {base:+.0f}% at ${v.target_mean:,.0f} | "
        f"bull 25%: {bull:+.0f}% at ${v.target_high:,.0f}; "
        f"scenarios anchored to Yahoo Finance analyst targets vs price ${v.price:,.2f})."
    )


def _right_size_insider_flags(report: DDReport) -> int:
    """Demote small, routine insider selling from red-flag to neutral context.

    Net sales below 0.05% of market cap are typical 10b5-1 activity for large
    companies and must not headline the executive summary or red flags.
    """
    ia = report.insider_activity
    market_cap = report.valuation.market_cap
    if not (ia.available and ia.net_value and market_cap):
        return 0
    pct = abs(ia.net_value) / market_cap * 100.0
    if pct >= 0.05:
        return 0
    changed = 0
    insider_words = ("insider", "form 4")
    bearish_words = ("bearish", "sell", "sale", "sold", "concern", "red flag")

    def is_bearish_insider(text: str) -> bool:
        lower = text.lower()
        return any(w in lower for w in insider_words) and any(w in lower for w in bearish_words)

    for claim in list(report.red_flags):
        if is_bearish_insider(claim.text) or "mkt_insiders" in claim.citation_chunk_ids:
            if report.drop_claim(claim.claim_id):
                changed += 1
    for claim in list(report.executive_summary):
        if is_bearish_insider(claim.text):
            if report.drop_claim(claim.claim_id):
                changed += 1

    def scrub(items: list[str]) -> list[str]:
        nonlocal changed
        kept = []
        for item in items:
            if is_bearish_insider(item):
                changed += 1
                continue
            kept.append(item)
        return kept

    report.institutional_summary.key_bear_thesis = scrub(report.institutional_summary.key_bear_thesis)
    report.investment_thesis.bear_case = scrub(report.investment_thesis.bear_case)

    direction = "selling" if (ia.net_shares < 0 or ia.net_value < 0) else "buying"
    if not any("% of market cap" in claim.text for claim in ia.commentary):
        ia.commentary.append(
            Claim(
                text=(
                    f"Net insider {direction} of ${abs(ia.net_value)/1e6:,.1f}M equals "
                    f"~{pct:.4f}% of market cap - routine in scale for a company this size "
                    "and treated as a neutral observation rather than a red flag."
                ),
                citation_chunk_ids=["mkt_insiders"],
                section="insider_activity",
            )
        )
        changed += 1
    return changed


def _repair_rating_and_score(report: DDReport) -> int:
    changed = 0
    score = _score_from_formula(report)
    rating = _rating_from_score(score, report)
    scorecard = report.quality_checks.final_scorecard

    # Hard gate: with fewer than 3 verified claims the run has no evidentiary
    # basis for a directional rating — suppress it and mark the report partial.
    verified_claims = len(report.all_claims())
    scorecard["Verified claims"] = verified_claims
    # Single source of truth: the prose counter must match this number.
    report.quality_checks.claim_verification = (
        f"{verified_claims} claims verified by citations and/or deterministic metrics."
    )
    if verified_claims < 3:
        if not report.metadata.partial:
            report.metadata.partial = True
            changed += 1
        if rating in _DIRECTIONAL_RATINGS:
            rating = _NEUTRAL_RATING
            report.metadata.warnings.append(
                f"RATING GATE: directional rating suppressed because only {verified_claims} "
                "claims were verified this run (minimum 3 for rating authority)."
            )
    score_key = "Overall Investment Score (0-100)"
    legacy_score = scorecard.pop("Overall Investment Score", None)
    if scorecard.get(score_key, legacy_score) != score:
        changed += 1
    scorecard[score_key] = score
    scorecard.setdefault(
        "Formula",
        "Overall Investment Score = scorecard composite / 5 * 100 minus "
        "8 points per high-severity risk, 4 points per red flag, and 10 points "
        "when core valuation data is unavailable.",
    )
    if report.institutional_summary.investment_rating != rating:
        changed += 1
        report.institutional_summary.investment_rating = rating
    if report.scorecard.available and report.scorecard.composite_label != rating:
        changed += 1
        report.scorecard.composite_label = rating

    confidence = _recalibrate_confidence(report)
    if verified_claims < 3:
        confidence = min(confidence, 35)
    if report.institutional_summary.confidence_score != confidence:
        changed += 1
        report.institutional_summary.confidence_score = confidence

    outcome = _scenario_outcome_from_valuation(report)
    current = report.investment_thesis.probability_weighted_outcome
    if outcome is not None:
        if current != outcome:
            report.investment_thesis.probability_weighted_outcome = outcome
            changed += 1
    elif not current.startswith(("Probability-weighted", "Not assessed")):
        report.investment_thesis.probability_weighted_outcome = (
            "Not assessed - insufficient valuation data for scenario weighting."
        )
        changed += 1
    return changed


def _section_isolation(report: DDReport) -> int:
    changed = 0
    missing: list[tuple[str, bool, str]] = [
        ("Executive Summary", bool(report.executive_summary), "no verified executive-summary claims survived QA"),
        ("Business Overview", bool(report.business_overview), "no verified business-overview claims survived QA"),
        ("Financial Analysis", bool(report.financial_health.table.metrics or report.financial_health.commentary), "no current financial metrics or commentary available"),
        ("Valuation", _has_core_valuation(report), "core valuation multiples and peer benchmarks unavailable"),
        ("Risk Matrix", bool(report.risk_matrix), "no verified risk entries available"),
        # Red Flags intentionally omitted: an empty section after the checks ran
        # is a clean result, not a data gap (unavailable inputs are disclosed
        # separately, e.g. the insider-data coverage note).
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


_PIPELINE_ERROR_MARKERS = (
    "report writer",
    "token budget",
    "did not complete",
    "pipeline",
    "structured output",
)


def _split_pipeline_errors(report: DDReport) -> int:
    """Infrastructure errors are not data gaps — move them to the QA appendix.

    'The tool broke' and 'the company lacks public information' are very
    different statements; mixing them misleads the reader about coverage.
    """
    moved = 0
    kept: list[str] = []
    for gap in report.data_gaps:
        if any(marker in gap.lower() for marker in _PIPELINE_ERROR_MARKERS):
            note = gap if gap.upper().startswith("PIPELINE") else f"PIPELINE: {gap}"
            if note not in report.metadata.warnings:
                report.metadata.warnings.append(note)
            moved += 1
        else:
            kept.append(gap)
    report.data_gaps = kept
    return moved


def _reconcile_earnings_quality(report: DDReport) -> int:
    """A quality label must be backed by data; otherwise it is 'Not assessed'."""
    eq = report.earnings_quality
    if eq.quality_label and eq.accruals_ratio is None and eq.cash_conversion is None:
        eq.quality_label = "Not assessed"
        return 1
    return 0


def run_final_report_qa(report: DDReport, state: AgentState | None = None, logger: Any | None = None) -> list[dict[str, str]]:
    """Repair final report consistency and emit internal QA statuses.

    Each pass runs exactly once, ordered so content repairs (sanity, insider
    scale, thesis top-up) land before the score/rating/confidence pass, which
    must reflect the final content.
    """
    statuses: list[dict[str, str]] = []

    def record(label: str, changed: int, reason: str) -> None:
        status = "fail" if changed else "pass"
        payload = {"check": label, "status": status, "reason": reason if changed else "ok"}
        statuses.append(payload)
        if logger is not None:
            logger.info("final_qa_status", **payload)

    pipeline_moved = _split_pipeline_errors(report)
    record("PIPELINE VS DATA GAPS", pipeline_moved, f"moved {pipeline_moved} infrastructure errors to the QA appendix")

    section_changes = _section_isolation(report)
    record("SECTION ISOLATION", section_changes, f"repaired {section_changes} section disclosures")

    sanity_removed = _remove_suspicious_metrics(report)
    record("DATA SANITY", sanity_removed, f"rejected {sanity_removed} suspicious metrics or dependent claims")

    stale_removed = _remove_stale_claims(report)
    record("CHECK 2 - Recency", stale_removed, f"removed {stale_removed} stale current-context claims")

    insider_changes = _right_size_insider_flags(report)
    record("INSIDER SCALE", insider_changes, f"right-sized {insider_changes} insider signals")

    eq_changes = _reconcile_earnings_quality(report)
    record("EARNINGS QUALITY LABEL", eq_changes, "downgraded unbacked quality label to Not assessed")

    contradiction_removed = _remove_dropped_claim_references(report)
    rating_consistent = (
        report.scorecard.composite_label == report.institutional_summary.investment_rating
        if report.scorecard.available and report.scorecard.composite_label
        else True
    )
    contradiction_changes = contradiction_removed + (0 if rating_consistent else 1)
    record("CHECK 3 - Contradiction", contradiction_changes, f"fixed {contradiction_changes} contradictions")

    duplicate_changes = _dedupe_sections(report)
    record("CHECK 5 - Duplication", duplicate_changes, f"removed {duplicate_changes} duplicate bullets")

    completeness_changes = _repair_minimum_content(report)
    record("CHECK 6 - Completeness", completeness_changes, f"repaired {completeness_changes} completeness issues")

    # Rating, score, confidence, and scenario outcome — after content is final.
    rating_changes = _repair_rating_and_score(report)
    record("CHECK 1 - Math", rating_changes, f"recomputed score/rating/confidence in {rating_changes} places")

    leak_changes = _scrub_raw_errors(report)
    record("CHECK 4 - Leakage", leak_changes, "scrubbed raw error leakage")

    report.data_gaps = _clean_list(report.data_gaps)
    report.metadata.warnings = _clean_list(report.metadata.warnings)
    return statuses

