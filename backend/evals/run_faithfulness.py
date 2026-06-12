"""Faithfulness eval: claim-level groundedness of a saved report.

Every claim with chunk citations is scored with the NLI model against its
cited chunks (fetched from Qdrant). Prints a per-section breakdown and
writes evals/results/faithfulness_<run_id>.json.

Usage:  python evals/run_faithfulness.py [path/to/report.json]
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from app.config import settings
from app.guardrails.grounding import best_entailment
from app.rag.retriever import fetch_chunks_by_ids
from app.report.schema import DDReport

RESULTS_DIR = Path("evals/results")


def latest_report() -> Path:
    reports = sorted(Path("data/reports").glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not reports:
        raise SystemExit("No report JSON found in data/reports/ — run the orchestrator first.")
    return reports[-1]


def main(argv: list[str]) -> int:
    path = Path(argv[0]) if argv else latest_report()
    report = DDReport.model_validate_json(path.read_text(encoding="utf-8"))
    print(f"Scoring report {path} (run {report.metadata.run_id})")

    cited_claims = [c for c in report.all_claims() if c.citation_chunk_ids]
    all_ids = [cid for c in cited_claims for cid in c.citation_chunk_ids]
    chunks = fetch_chunks_by_ids(all_ids)

    per_section: dict[str, list[float]] = defaultdict(list)
    details: list[dict[str, object]] = []
    for claim in cited_claims:
        premises = [
            chunks[cid].text for cid in claim.citation_chunk_ids if cid in chunks
        ]
        score = best_entailment(claim.text, premises) if premises else 0.0
        grounded = score >= settings.critic_entailment_threshold
        per_section[claim.section or "unknown"].append(score)
        details.append(
            {
                "claim_id": claim.claim_id,
                "section": claim.section,
                "text": claim.text,
                "score": round(score, 4),
                "grounded": grounded,
            }
        )

    n_grounded = sum(1 for d in details if d["grounded"])
    total = len(details)
    print(f"\nGrounded claims: {n_grounded}/{total} = "
          f"{(n_grounded / total * 100) if total else 0:.1f}%")
    print("\nPer-section breakdown:")
    for section, scores in sorted(per_section.items()):
        ok = sum(1 for s in scores if s >= settings.critic_entailment_threshold)
        print(f"  {section:24s} {ok}/{len(scores)} grounded "
              f"(mean score {sum(scores) / len(scores):.3f})")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"faithfulness_{report.metadata.run_id}.json"
    out.write_text(
        json.dumps(
            {
                "run_id": report.metadata.run_id,
                "grounded": n_grounded,
                "total": total,
                "threshold": settings.critic_entailment_threshold,
                "claims": details,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
