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
    all_revenue_years = sorted(revenue)
    years = all_revenue_years[-4:]

    if all_revenue_years:
        latest = all_revenue_years[-1]
        metrics.append(
            MetricValue(
                metric_id="revenue",
                name=f"Revenue FY{latest}",
                value=round(revenue[latest], 0),
                unit="USD",
                period=f"FY{latest}",
                inputs={f"revenue_fy{latest}": revenue[latest]},
                formula="reported revenue",
            )
        )

    for span_years, metric_id in ((5, "revenue_cagr_5y"), (10, "revenue_cagr_10y")):
        if len(all_revenue_years) >= 2:
            latest = all_revenue_years[-1]
            target_start = latest - span_years
            eligible = [year for year in all_revenue_years if year <= target_start]
            if eligible:
                first = eligible[-1]
                span = latest - first
                growth = cagr(revenue[first], revenue[latest], span)
                if growth is not None:
                    metrics.append(
                        MetricValue(
                            metric_id=metric_id,
                            name=f"Revenue CAGR FY{first}->FY{latest}",
                            value=round(growth, 2),
                            unit="%",
                            period=f"FY{first}-FY{latest}",
                            inputs={
                                f"revenue_fy{first}": revenue[first],
                                f"revenue_fy{latest}": revenue[latest],
                            },
                            formula=f"((end/begin)^(1/{span}) - 1) * 100",
                        )
                    )

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
    for metric_id, label, source in (
        ("gross_profit", "Gross profit", gross_profit),
        ("operating_income", "Operating income", _by_year(facts.facts.get("operating_income", []))),
        ("net_income", "Net income", _by_year(facts.facts.get("net_income", []))),
    ):
        if source:
            y = sorted(source)[-1]
            metrics.append(
                MetricValue(
                    metric_id=metric_id,
                    name=f"{label} FY{y}",
                    value=round(source[y], 0),
                    unit="USD",
                    period=f"FY{y}",
                    inputs={f"{metric_id}_fy{y}": source[y]},
                    formula=f"reported {label.lower()}",
                )
            )
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
    # Intersect with recent revenue years (last 4) to avoid stale XBRL data from old filings
    recent_revenue_years = set(years)  # years already capped to last 4
    fcf_years = sorted((set(ocf) & set(capex)) & recent_revenue_years)
    if not fcf_years:
        # Fallback: most recent overlap across all available years
        fcf_years = sorted(set(ocf) & set(capex))[-1:]
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

    # ── ROIC (Return on Invested Capital) ────────────────────
    net_income = _by_year(facts.facts.get("net_income", []))
    total_assets = _by_year(facts.facts.get("assets_total", []))
    total_liab = _by_year(facts.facts.get("liabilities_total", []))
    # Invested capital = total assets - total liabilities (≈ shareholders equity)
    # This avoids the near-zero denominator issue when current_liabilities ≈ total_assets
    roic_years = sorted(set(net_income) & set(total_assets) & set(total_liab))
    if roic_years:
        y = roic_years[-1]
        invested_capital = total_assets[y] - total_liab[y]  # equity proxy
        if invested_capital > 0 and y in net_income:
            roic = net_income[y] / invested_capital * 100.0
            metrics.append(
                MetricValue(
                    metric_id="roic",
                    name=f"ROIC FY{y}",
                    value=round(roic, 2),
                    unit="%",
                    period=f"FY{y}",
                    inputs={"net_income": net_income[y],
                            "invested_capital": invested_capital},
                    formula="net_income / (total_assets - total_liabilities) * 100",
                )
            )
            if roic < 0:
                anomalies.append(f"Negative ROIC of {roic:.1f}% in FY{y} — capital destroying.")
            elif roic > 20:
                pass  # note positive ROIC but not an anomaly

    # ── Earnings quality (accruals ratio) ────────────────────
    # Accruals ratio = (Net Income - Operating Cash Flow) / Revenue
    # Closer to 0 = higher quality; high positive = earnings not backed by cash.
    eq_years = sorted(set(net_income) & set(ocf) & set(revenue))
    if eq_years:
        y = eq_years[-1]
        if revenue[y] != 0:
            accruals = (net_income[y] - ocf[y]) / revenue[y] * 100.0
            metrics.append(
                MetricValue(
                    metric_id="accruals_ratio",
                    name=f"Accruals ratio FY{y}",
                    value=round(accruals, 2),
                    unit="%",
                    period=f"FY{y}",
                    inputs={"net_income": net_income[y], "operating_cash_flow": ocf[y],
                            "revenue": revenue[y]},
                    formula="(net_income - operating_cash_flow) / revenue * 100",
                )
            )
            if accruals > 10:
                anomalies.append(
                    f"High accruals ratio of {accruals:.1f}% in FY{y} — earnings quality concern."
                )

    # ── Cash conversion (OCF / Net Income) ───────────────────
    # Ratio > 1 means cash earnings exceed accounting earnings.
    cc_years = sorted(set(net_income) & set(ocf))
    if cc_years:
        y = cc_years[-1]
        if net_income[y] != 0:
            cash_conv = ocf[y] / net_income[y]
            metrics.append(
                MetricValue(
                    metric_id="cash_conversion",
                    name=f"Cash conversion FY{y}",
                    value=round(cash_conv, 2),
                    unit="x",
                    period=f"FY{y}",
                    inputs={"operating_cash_flow": ocf[y], "net_income": net_income[y]},
                    formula="operating_cash_flow / net_income",
                )
            )
            if cash_conv < 0.8 and net_income[y] > 0:
                anomalies.append(
                    f"Cash conversion of {cash_conv:.2f}x in FY{y} — OCF significantly "
                    "below reported earnings."
                )

    return FinancialAnalysis(metrics=metrics, anomalies=anomalies)
