"""Numeric eval: every metric in a saved report vs freshly recomputed XBRL values.

Usage:  python evals/run_numeric_check.py [path/to/report.json]
        (defaults to the newest JSON in data/reports/)

Exits nonzero unless 100% of report numbers match the recomputed source.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.report.schema import DDReport
from app.tools.metrics import compute_metrics
from app.tools.xbrl import fetch_financial_facts


def latest_report() -> Path:
    reports = sorted(Path("data/reports").glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not reports:
        raise SystemExit("No report JSON found in data/reports/ — run the orchestrator first.")
    return reports[-1]


def main(argv: list[str]) -> int:
    path = Path(argv[0]) if argv else latest_report()
    report = DDReport.model_validate_json(path.read_text(encoding="utf-8"))
    print(f"Checking report {path} ({report.company.ticker}, run {report.metadata.run_id})")

    facts = fetch_financial_facts(report.company.cik)
    recomputed = {m.metric_id: m for m in compute_metrics(facts).metrics}

    total = matched = 0
    for metric in report.financial_health.table.metrics:
        total += 1
        fresh = recomputed.get(metric.metric_id)
        if fresh is None:
            print(f"  MISSING  {metric.metric_id}: not recomputable from current XBRL")
            continue
        if fresh.value == metric.value:
            matched += 1
        else:
            print(
                f"  MISMATCH {metric.metric_id}: report={metric.value} "
                f"recomputed={fresh.value}"
            )

    if total == 0:
        print("Report contains no metrics — nothing to verify.")
        return 1
    pct = matched / total * 100
    print(f"\nExact-match: {matched}/{total} = {pct:.1f}%")
    return 0 if matched == total else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
