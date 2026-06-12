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
        "",
    ]

    lines += _render_section("Executive Summary", report.executive_summary)
    lines += _render_section("Business Overview", report.business_overview)

    lines += ["## Financial Health", "", "| Metric | Value | Period |", "|---|---|---|"]
    for metric in report.financial_health.table.metrics:
        lines.append(
            f"| {metric.name} | {_format_value(metric.value, metric.unit)} | {metric.period} |"
        )
    lines.append("")
    if report.financial_health.commentary:
        lines.extend(f"- {_render_claim(c)}" for c in report.financial_health.commentary)
        lines.append("")

    lines += ["## Risk Matrix", ""]
    if not report.risk_matrix:
        lines += ["_No risks identified._", ""]
    for risk in report.risk_matrix:
        lines.append(f"### {risk.title} — severity: {risk.severity}, likelihood: {risk.likelihood}")
        lines.extend(f"- {_render_claim(c)}" for c in risk.claims)
        lines.append("")

    lines += _render_section("Recent Developments", report.recent_developments)
    lines += _render_section("Red Flags", report.red_flags)

    lines += ["## Data Gaps", ""]
    if report.data_gaps:
        lines.extend(f"- {gap}" for gap in report.data_gaps)
    else:
        lines.append("_None — all sources were available._")
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
