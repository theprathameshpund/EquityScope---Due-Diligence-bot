"""DDReport → Markdown with citation anchors."""

from __future__ import annotations

from app.report.schema import DDReport
from app.state import Claim
from typing import Any


def _safe_fmt(value: Any, fmt: str, default: str = "n/a") -> str:
    """Format numeric values safely; return `default` when value is None."""
    if value is None:
        return default
    try:
        return format(value, fmt)
    except Exception:
        return default


def _format_value(value: float | None, unit: str) -> str:
    """Human-friendly rendering for numeric metric values. Returns 'n/a' for missing values."""
    if value is None:
        return "n/a"
    if unit == "USD" and abs(value) >= 1e9:
        return f"${value / 1e9:,.1f}B"
    if unit == "USD" and abs(value) >= 1e6:
        return f"${value / 1e6:,.1f}M"
    if unit == "USD":
        return f"${value:,.0f}"
    if unit == "%":
        return f"{value:,.2f}%"
    if unit == "x":
        return f"{value:,.2f}x"
    if unit == "bps":
        return f"{value:+,.0f}bps"
    return f"{value:,.2f} {unit}"


def _render_claim(claim: Claim) -> str:
    anchors = [f"[^{cid}]" for cid in claim.citation_chunk_ids]
    anchors += [f"`{mid}`" for mid in claim.metric_ids]
    suffix = " " + "".join(anchors) if anchors else ""
    return f"{claim.text}{suffix}"


def _render_section(title: str, claims: list[Claim]) -> list[str]:
    lines = [f"## {title}", ""]
    if not claims:
        lines.append("_No verified content for this section._")
    else:
        lines.extend(f"- {_render_claim(c)}" for c in claims)
    lines.append("")
    return lines


def render_markdown(report: DDReport) -> str:
    """Render the full report as GitHub-flavored Markdown."""
    meta = report.metadata
    lines: list[str] = [
        f"# Due Diligence Report — {report.company.name} ({report.company.ticker})",
        "",
        f"_CIK {report.company.cik} · run `{meta.run_id}` · generated {meta.generated_at:%Y-%m-%d %H:%M} UTC_",
    ]
    if report.company.sector:
        lines.append(f"_Sector: {report.company.sector} / {report.company.industry}_")
    lines.append("")

    inst = report.institutional_summary
    lines += [
        "## Institutional Decision Summary",
        "",
        f"- Investment rating: **{inst.investment_rating}**",
        f"- Confidence score: **{inst.confidence_score}/100**",
        f"- Investment horizon: **{inst.investment_horizon}**",
        f"- Expected return range: {inst.expected_return_range}",
        "",
        "**Key Bull Thesis**",
        "",
        *[f"- {item}" for item in inst.key_bull_thesis],
        "",
        "**Key Bear Thesis**",
        "",
        *[f"- {item}" for item in inst.key_bear_thesis],
        "",
        "**Top Catalysts**",
        "",
        *[f"- {item}" for item in inst.top_catalysts],
        "",
        "**Top Risks**",
        "",
        *[f"- {item}" for item in inst.top_risks],
        "",
    ]

    # ── Investment Scorecard ────────────────────────────────────
    sc = report.scorecard
    if sc.available and sc.composite_label:
        lines += [
            "## Investment Scorecard",
            "",
            f"**Composite: {sc.composite_label} ({sc.composite_score:.1f}/5.0)**",
            "",
            "| Dimension | Score | Rationale |",
            "|---|---|---|",
        ]
        for d in sc.dimensions:
            bar = "█" * d["score"] + "░" * (5 - d["score"])
            lines.append(f"| {d['name']} | {d['score']}/5 {bar} | {d['rationale']} |")
        lines.append("")

    lines += _render_section("Executive Summary", report.executive_summary)
    lines += _render_section("Business Overview", report.business_overview)

    for title, section in (
        ("Business Quality Analysis", report.business_quality),
        ("Management Analysis", report.management_analysis),
        ("Segment Analysis", report.segment_analysis),
        ("Industry Analysis", report.industry_analysis),
    ):
        lines += [f"## {title}", ""]
        if section.score is not None:
            lines.append(f"**Score**: {section.score:.1f}/10")
            lines.append("")
        if section.summary:
            lines.extend(f"- {item}" for item in section.summary)
            lines.append("")
        if section.data_unavailable:
            lines.append("**Data unavailable or unverifiable**")
            lines.append("")
            lines.extend(f"- {item}" for item in section.data_unavailable)
            lines.append("")

    # ── Financial Health ────────────────────────────────────────
    lines += ["## Financial Health", "", "| Metric | Value | Period |", "|---|---|---|"]
    for metric in report.financial_health.table.metrics:
        lines.append(
            f"| {metric.name} | {_format_value(metric.value, metric.unit)} | {metric.period} |"
        )
    lines.append("")
    if report.financial_health.commentary:
        lines.extend(f"- {_render_claim(c)}" for c in report.financial_health.commentary)
        lines.append("")

    # ── DCF / Reverse DCF ──────────────────────────────────────
    dcf = report.dcf_analysis
    lines += ["## DCF and Reverse DCF", ""]
    for case in (dcf.base_case, dcf.bull_case, dcf.bear_case):
        lines.append(f"### {case.name}")
        if case.intrinsic_value is not None:
            lines.append(f"- Intrinsic value: ${case.intrinsic_value:,.2f}")
        if case.expected_return_pct is not None:
            lines.append(f"- Expected return: {case.expected_return_pct:+.1f}%")
        lines.append(f"- Status: {case.status}")
        if case.assumptions:
            lines.extend(f"- Assumption: {assumption}" for assumption in case.assumptions)
        lines.append("")
    lines += [
        f"- Reverse DCF: {dcf.reverse_dcf}",
        f"- Margin of safety: {dcf.margin_of_safety}",
        "",
    ]

    # ── Valuation & Analyst Consensus ──────────────────────────
    v = report.valuation
    val_rows: list[tuple[str, str]] = []
    if v.pe_ttm is not None:
        val_rows.append(("Trailing P/E", _safe_fmt(v.pe_ttm, ".1f") + "x"))
    if v.forward_pe is not None:
        val_rows.append(("Forward P/E", _safe_fmt(v.forward_pe, ".1f") + "x"))
    if v.ev_to_ebitda is not None:
        val_rows.append(("EV/EBITDA", _safe_fmt(v.ev_to_ebitda, ".1f") + "x"))
    if v.price_to_sales is not None:
        val_rows.append(("Price/Sales", _safe_fmt(v.price_to_sales, ".1f") + "x"))
    if v.price_to_book is not None:
        val_rows.append(("Price/Book", _safe_fmt(v.price_to_book, ".1f") + "x"))
    if v.beta is not None:
        val_rows.append(("Beta", _safe_fmt(v.beta, ".2f")))
    if v.dividend_yield is not None:
        val_rows.append(("Dividend yield", _safe_fmt(v.dividend_yield * 100, ".2f") + "%"))
    if v.payout_ratio is not None:
        val_rows.append(("Payout ratio", _safe_fmt(v.payout_ratio * 100, ".1f") + "%"))
    if v.short_percent_float is not None:
        val_rows.append(("Short interest", _safe_fmt(v.short_percent_float * 100, ".1f") + "% of float"))
    if v.short_ratio is not None:
        val_rows.append(("Short ratio (days to cover)", _safe_fmt(v.short_ratio, ".1f")))
    if val_rows or v.target_mean is not None or v.recommendation:
        lines += ["## Valuation & Market Data", ""]
        if val_rows:
            lines += ["| Metric | Value |", "|---|---|"]
            lines.extend(f"| {k} | {val_} |" for k, val_ in val_rows)
            lines.append("")
        if v.recommendation:
            rec_label = v.recommendation.replace("_", " ").title()
            lines.append(
                f"**Analyst Consensus**: {rec_label}"
                + (f" (mean score {_safe_fmt(v.recommendation_mean, '.1f')}/5)" if v.recommendation_mean is not None else "")
                + (f" · {v.num_analysts} analysts" if v.num_analysts else "")
            )
        if v.target_mean is not None:
            high_low = (
                f" · high ${_safe_fmt(v.target_high, '.2f')} · low ${_safe_fmt(v.target_low, '.2f')}"
                if (v.target_high is not None and v.target_low is not None)
                else ""
            )
            lines.append(f"**12-Month Price Target**: mean ${_safe_fmt(v.target_mean, '.2f')}" + high_low)
        if v.peers:
            lines += ["", "**Peer Comparison**", "", "| Peer | P/E | P/S | EV/EBITDA | P/B |",
                      "|---|---|---|---|---|"]
            for p in v.peers:
                def _m(val):
                    return (_safe_fmt(val, ".1f") + "x") if val is not None else "—"

                pe = _m(p.pe_ttm)
                ps = _m(p.price_to_sales)
                ev = _m(p.ev_to_ebitda)
                pb = _safe_fmt(p.price_to_book, ".1f") if p.price_to_book is not None else "—"
                lines.append(f"| {p.ticker} | {pe} | {ps} | {ev} | {pb} |")
        if v.commentary:
            lines.append("")
            lines.extend(f"- {_render_claim(c)}" for c in v.commentary)
        lines.append("")

    # ── Earnings Quality ────────────────────────────────────────
    eq = report.earnings_quality
    if eq.accruals_ratio is not None or eq.cash_conversion is not None:
        lines += ["## Earnings Quality", ""]
        if eq.quality_label:
            lines.append(f"**Quality Label**: {eq.quality_label}")
        if eq.accruals_ratio is not None:
            lines.append(f"- **Accruals ratio**: {eq.accruals_ratio:.2f}%  "
                         "(lower = better; earnings backed by cash)")
        if eq.cash_conversion is not None:
            lines.append(f"- **Cash conversion**: {eq.cash_conversion:.2f}x  "
                         "(ratio of OCF to net income; >1 is ideal)")
        if eq.flags:
            lines.append("")
            lines.extend(f"> ⚠️ {flag}" for flag in eq.flags)
        if eq.commentary:
            lines.append("")
            lines.extend(f"- {_render_claim(c)}" for c in eq.commentary)
        lines.append("")

    # ── Risk Matrix ─────────────────────────────────────────────
    lines += ["## Risk Matrix", ""]
    if not report.risk_matrix:
        lines += ["_No risks identified._", ""]
    for risk in report.risk_matrix:
        lines.append(f"### {risk.title} — severity: {risk.severity}, likelihood: {risk.likelihood}")
        lines.extend(f"- {_render_claim(c)}" for c in risk.claims)
        lines.append(f"- Mitigation: {risk.mitigation}")
        if risk.monitoring_metrics:
            lines.append("- Monitoring metrics: " + "; ".join(risk.monitoring_metrics))
        lines.append("")

    lines += _render_section("Recent Developments", report.recent_developments)
    lines += _render_section("Red Flags", report.red_flags)

    # ── Investment Thesis ─────────────────────────────────────
    thesis = report.investment_thesis
    lines += ["## Investment Thesis", ""]
    for title, items in (
        ("Bull Case", thesis.bull_case),
        ("Base Case", thesis.base_case),
        ("Bear Case", thesis.bear_case),
        ("Monitoring Metrics", thesis.monitoring_metrics),
        ("Upgrade Triggers", thesis.upgrade_triggers),
        ("Downgrade Triggers", thesis.downgrade_triggers),
        ("Exit Triggers", thesis.exit_triggers),
    ):
        lines.append(f"**{title}**")
        lines.append("")
        lines.extend(f"- {item}" for item in items)
        lines.append("")
    lines.append(f"**Probability Weighted Outcome**: {thesis.probability_weighted_outcome}")
    lines.append("")

    # ── Insider Activity ────────────────────────────────────────
    ia = report.insider_activity
    if ia.available and ia.transactions:
        lines += ["## Insider Activity (SEC Form 4)", ""]
        sentiment_icon = {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}.get(ia.sentiment, "")
        lines.append(
            f"**Net sentiment**: {sentiment_icon} {ia.sentiment.title()}  |  "
            f"Net shares: {ia.net_shares:+,.0f}  |  "
            f"Net value: ${ia.net_value/1e6:+,.1f}M"
        )
        lines += ["", "| Insider | Title | Type | Shares | Date |", "|---|---|---|---|---|"]
        for t in ia.transactions[:10]:
            icon = "🟢" if t.transaction_type == "Purchase" else "🔴"
            lines.append(
                f"| {t.name} | {t.title or '—'} | {icon} {t.transaction_type} "
                f"| {t.shares:,.0f} | {t.date} |"
            )
        if ia.commentary:
            lines.append("")
            lines.extend(f"- {_render_claim(c)}" for c in ia.commentary)
        lines.append("")

    # ── Management Questions ────────────────────────────────────
    if report.management_questions:
        lines += ["## Key Questions for Management", ""]
        lines.extend(f"{i+1}. {q}" for i, q in enumerate(report.management_questions))
        lines.append("")

    # ── Data Gaps ───────────────────────────────────────────────
    lines += ["## Data Gaps", ""]
    if report.data_gaps:
        lines.extend(f"- {gap}" for gap in report.data_gaps)
    else:
        lines.append("_None — all sources were available._")
    lines.append("")

    # ── Run Metadata ────────────────────────────────────────────
    lines += [
        "## Report Quality Checks",
        "",
        f"- Claim verification: {report.quality_checks.claim_verification}",
        f"- Source policy: {report.quality_checks.source_policy}",
        f"- Stale data policy: {report.quality_checks.stale_data_policy}",
        f"- Unavailable-data policy: {report.quality_checks.unavailable_policy}",
        "",
        "| Dimension | Score / Label |",
        "|---|---|",
    ]
    for key, value in report.quality_checks.final_scorecard.items():
        lines.append(f"| {key} | {value} |")
    lines.append("")

    lines += [
        "## Run Metadata",
        "",
        f"- Duration: {meta.duration_s:.1f}s",
        f"- Tokens used: {meta.tokens_used:,}",
        f"- Approx. cost: ${meta.cost_usd:.4f}",
        f"- Models: {', '.join(f'{k}={v}' for k, v in meta.model_versions.items()) or 'n/a'}",
    ]
    if meta.warnings:
        lines.append("- Warnings:")
        lines.extend(f"  - {w}" for w in meta.warnings)
    lines.append("")
    return "\n".join(lines)
