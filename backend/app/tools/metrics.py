"""Deterministic financial metric computation — pure functions, no LLM.

Every MetricValue carries the raw XBRL input values used to compute it,
so the critic and the numeric eval can re-verify each number exactly.
"""

from __future__ import annotations

from app.state import FactValue, FinancialAnalysis, FinancialFacts, MetricValue

# Anomaly rule thresholds (basis points / ratios) — deterministic flags.
MARGIN_DROP_BPS_FLAG = 300.0
DEBT_TO_EBITDA_FLAG = 3.0
CURRENT_RATIO_FLAG = 1.0
DILUTION_PCT_FLAG = 5.0


def _latest_years(values: list[FactValue], n: int) -> list[FactValue]:
    return sorted(values, key=lambda v: v.fiscal_year)[-n:]


def _by_year(values: list[FactValue]) -> dict[int, float]:
    return {v.fiscal_year: v.value for v in values}


def cagr(begin: float, end: float, years: int) -> float | None:
    """Compound annual growth rate in percent; None when undefined."""
    if begin <= 0 or end <= 0 or years <= 0:
        return None
    return float(((end / begin) ** (1.0 / years) - 1.0) * 100.0)


def pct_margin(numerator: float, revenue: float) -> float | None:
    if revenue == 0:
        return None
    return numerator / revenue * 100.0


def compute_metrics(facts: FinancialFacts) -> FinancialAnalysis:
    """Compute all report metrics from typed XBRL facts."""
    metrics: list[MetricValue] = []
    anomalies: list[str] = []

    revenue = _by_year(facts.facts.get("revenue", []))
    years = sorted(revenue)[-4:]

    # ── Revenue growth (3y CAGR + YoY) ────────────────────────
    if len(years) >= 2:
        latest, first = years[-1], years[0]
        span = latest - first
        growth = cagr(revenue[first], revenue[latest], span)
        if growth is not None:
            metrics.append(
                MetricValue(
                    metric_id="revenue_cagr_3y",
                    name=f"Revenue CAGR FY{first}->FY{latest}",
                    value=round(growth, 2),
                    unit="%",
                    period=f"FY{first}-FY{latest}",
                    inputs={f"revenue_fy{first}": revenue[first],
                            f"revenue_fy{latest}": revenue[latest]},
                    formula=f"((end/begin)^(1/{span}) - 1) * 100",
                )
            )
        prev = years[-2]
        if revenue[prev] != 0:
            yoy = (revenue[latest] / revenue[prev] - 1.0) * 100.0
            metrics.append(
                MetricValue(
                    metric_id="revenue_growth_yoy",
                    name=f"Revenue growth FY{prev}->FY{latest}",
                    value=round(yoy, 2),
                    unit="%",
                    period=f"FY{latest}",
                    inputs={f"revenue_fy{prev}": revenue[prev],
                            f"revenue_fy{latest}": revenue[latest]},
                    formula="(latest/prev - 1) * 100",
                )
            )
            if yoy < 0:
                anomalies.append(f"Revenue declined {abs(round(yoy, 1))}% YoY in FY{latest}.")

    # ── Margins and their YoY trends ──────────────────────────
    gross_profit = _by_year(facts.facts.get("gross_profit", []))
    if not gross_profit:
        cost = _by_year(facts.facts.get("cost_of_revenue", []))
        gross_profit = {
            y: revenue[y] - cost[y] for y in revenue if y in cost
        }
    margin_sources = {
        "gross_margin": gross_profit,
        "operating_margin": _by_year(facts.facts.get("operating_income", [])),
        "net_margin": _by_year(facts.facts.get("net_income", [])),
    }
    for margin_id, source in margin_sources.items():
        margin_years = [y for y in years if y in source and y in revenue]
        if not margin_years:
            continue
        per_year: dict[int, float] = {}
        for y in margin_years:
            m = pct_margin(source[y], revenue[y])
            if m is None:
                continue
            per_year[y] = m
            metrics.append(
                MetricValue(
                    metric_id=f"{margin_id}_fy{y}",
                    name=f"{margin_id.replace('_', ' ').title()} FY{y}",
                    value=round(m, 2),
                    unit="%",
                    period=f"FY{y}",
                    inputs={"numerator": source[y], "revenue": revenue[y]},
                    formula="numerator / revenue * 100",
                )
            )
        if len(per_year) >= 2:
            ordered = sorted(per_year)
            delta_bps = (per_year[ordered[-1]] - per_year[ordered[-2]]) * 100.0
            metrics.append(
                MetricValue(
                    metric_id=f"{margin_id}_trend_bps",
                    name=f"{margin_id.replace('_', ' ').title()} YoY change",
                    value=round(delta_bps, 1),
                    unit="bps",
                    period=f"FY{ordered[-2]}->FY{ordered[-1]}",
                    inputs={f"margin_fy{ordered[-2]}": per_year[ordered[-2]],
                            f"margin_fy{ordered[-1]}": per_year[ordered[-1]]},
                    formula="(margin_latest - margin_prev) * 100",
                )
            )
            if delta_bps < -MARGIN_DROP_BPS_FLAG:
                anomalies.append(
                    f"{margin_id.replace('_', ' ').title()} fell "
                    f"{abs(round(delta_bps))}bps YoY (threshold {MARGIN_DROP_BPS_FLAG:.0f}bps)."
                )

    # ── Leverage ──────────────────────────────────────────────
    op_income = _by_year(facts.facts.get("operating_income", []))
    dep_amort = _by_year(facts.facts.get("depreciation_amortization", []))
    lt_debt = _by_year(facts.facts.get("long_term_debt", []))
    lt_debt_cur = _by_year(facts.facts.get("long_term_debt_current", []))
    if lt_debt:
        debt_year = max(lt_debt)
        total_debt = lt_debt[debt_year] + lt_debt_cur.get(debt_year, 0.0)
        if debt_year in op_income and debt_year in dep_amort:
            ebitda = op_income[debt_year] + dep_amort[debt_year]
            if ebitda > 0:
                ratio = total_debt / ebitda
                metrics.append(
                    MetricValue(
                        metric_id="debt_to_ebitda",
                        name=f"Total debt / EBITDA FY{debt_year}",
                        value=round(ratio, 2),
                        unit="x",
                        period=f"FY{debt_year}",
                        inputs={
                            "long_term_debt": lt_debt[debt_year],
                            "long_term_debt_current": lt_debt_cur.get(debt_year, 0.0),
                            "operating_income": op_income[debt_year],
                            "depreciation_amortization": dep_amort[debt_year],
                        },
                        formula="(lt_debt + lt_debt_current) / (op_income + d_and_a)",
                    )
                )
                if ratio > DEBT_TO_EBITDA_FLAG:
                    anomalies.append(
                        f"Debt/EBITDA of {ratio:.1f}x exceeds {DEBT_TO_EBITDA_FLAG}x."
                    )

    assets_cur = _by_year(facts.facts.get("assets_current", []))
    liab_cur = _by_year(facts.facts.get("liabilities_current", []))
    common = sorted(set(assets_cur) & set(liab_cur))
    if common:
        y = common[-1]
        if liab_cur[y] != 0:
            ratio = assets_cur[y] / liab_cur[y]
            metrics.append(
                MetricValue(
                    metric_id="current_ratio",
                    name=f"Current ratio FY{y}",
                    value=round(ratio, 2),
                    unit="x",
                    period=f"FY{y}",
                    inputs={"assets_current": assets_cur[y],
                            "liabilities_current": liab_cur[y]},
                    formula="assets_current / liabilities_current",
                )
            )
            if ratio < CURRENT_RATIO_FLAG:
                anomalies.append(
                    f"Current ratio of {ratio:.2f} is below {CURRENT_RATIO_FLAG:.1f}."
                )

    # ── Free cash flow ────────────────────────────────────────
    ocf = _by_year(facts.facts.get("operating_cash_flow", []))
    capex = _by_year(facts.facts.get("capex", []))
    fcf_years = sorted(set(ocf) & set(capex))
    if fcf_years:
        y = fcf_years[-1]
        fcf = ocf[y] - capex[y]
        metrics.append(
            MetricValue(
                metric_id="fcf",
                name=f"Free cash flow FY{y}",
                value=round(fcf, 0),
                unit="USD",
                period=f"FY{y}",
                inputs={"operating_cash_flow": ocf[y], "capex": capex[y]},
                formula="operating_cash_flow - capex",
            )
        )
        if y in revenue and revenue[y] != 0:
            fcf_margin = fcf / revenue[y] * 100.0
            metrics.append(
                MetricValue(
                    metric_id="fcf_margin",
                    name=f"FCF margin FY{y}",
                    value=round(fcf_margin, 2),
                    unit="%",
                    period=f"FY{y}",
                    inputs={"fcf": fcf, "revenue": revenue[y]},
                    formula="fcf / revenue * 100",
                )
            )

    # ── Share dilution ────────────────────────────────────────
    shares = _by_year(facts.facts.get("diluted_shares", []))
    share_years = sorted(shares)[-4:]
    if len(share_years) >= 2:
        first_y, last_y = share_years[0], share_years[-1]
        if shares[first_y] > 0:
            span = last_y - first_y
            total_pct = (shares[last_y] / shares[first_y] - 1.0) * 100.0
            metrics.append(
                MetricValue(
                    metric_id="share_dilution",
                    name=f"Diluted share count change FY{first_y}->FY{last_y}",
                    value=round(total_pct, 2),
                    unit="%",
                    period=f"FY{first_y}-FY{last_y}",
                    inputs={f"shares_fy{first_y}": shares[first_y],
                            f"shares_fy{last_y}": shares[last_y]},
                    formula="(shares_end / shares_begin - 1) * 100",
                )
            )
            if span > 0 and total_pct / span > DILUTION_PCT_FLAG:
                anomalies.append(
                    f"Share dilution averaging {total_pct / span:.1f}%/yr exceeds "
                    f"{DILUTION_PCT_FLAG:.0f}%/yr."
                )

    return FinancialAnalysis(metrics=metrics, anomalies=anomalies)
