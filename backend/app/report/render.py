"""DDReport → Markdown with citation anchors."""

from __future__ import annotations

from app.report.schema import DDReport
from app.state import Claim


def _format_value(value: float, unit: str) -> str:
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

    # ── Valuation & Analyst Consensus ──────────────────────────
    v = report.valuation
    val_rows: list[tuple[str, str]] = []
    if v.pe_ttm:       val_rows.append(("Trailing P/E",   f"{v.pe_ttm:.1f}x"))
    if v.forward_pe:   val_rows.append(("Forward P/E",    f"{v.forward_pe:.1f}x"))
    if v.ev_to_ebitda: val_rows.append(("EV/EBITDA",      f"{v.ev_to_ebitda:.1f}x"))
    if v.price_to_sales: val_rows.append(("Price/Sales",  f"{v.price_to_sales:.1f}x"))
    if v.price_to_book:  val_rows.append(("Price/Book",   f"{v.price_to_book:.1f}x"))
    if v.beta:           val_rows.append(("Beta",          f"{v.beta:.2f}"))
    if v.dividend_yield: val_rows.append(("Dividend yield", f"{v.dividend_yield*100:.2f}%"))
    if v.payout_ratio:   val_rows.append(("Payout ratio",  f"{v.payout_ratio*100:.1f}%"))
    if v.short_percent_float:
        val_rows.append(("Short interest", f"{v.short_percent_float*100:.1f}% of float"))
    if v.short_ratio:
        val_rows.append(("Short ratio (days to cover)", f"{v.short_ratio:.1f}"))
    if val_rows or v.target_mean or v.recommendation:
        lines += ["## Valuation & Market Data", ""]
        if val_rows:
            lines += ["| Metric | Value |", "|---|---|"]
            lines.extend(f"| {k} | {val_} |" for k, val_ in val_rows)
            lines.append("")
        if v.recommendation:
            rec_label = v.recommendation.replace("_", " ").title()
            lines.append(
                f"**Analyst Consensus**: {rec_label}"
                + (f" (mean score {v.recommendation_mean:.1f}/5)" if v.recommendation_mean else "")
                + (f" · {v.num_analysts} analysts" if v.num_analysts else "")
            )
        if v.target_mean:
            lines.append(
                f"**12-Month Price Target**: mean ${v.target_mean:.2f}"
                + (f" · high ${v.target_high:.2f} · low ${v.target_low:.2f}"
                   if v.target_high and v.target_low else "")
            )
        if v.peers:
            lines += ["", "**Peer Comparison**", "", "| Peer | P/E | P/S | EV/EBITDA | P/B |",
                      "|---|---|---|---|---|"]
            for p in v.peers:
                lines.append(
                    f"| {p.ticker} | {p.pe_ttm:.1f}x | {p.price_to_sales:.1f}x "
                    f"| {p.ev_to_ebitda:.1f}x | {p.price_to_book:.1f}x |".replace(
                        " None.0x", " —").replace(" Nonex", " —")
                )
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
        lines.append("")

    lines += _render_section("Recent Developments", report.recent_developments)
    lines += _render_section("Red Flags", report.red_flags)

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
