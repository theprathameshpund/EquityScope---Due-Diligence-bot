"""Retrieval eval: hit-rate@5 and MRR over the golden Q&A set,
with and without the cross-encoder reranker.

A question counts as a hit when any expected phrase appears (case-insensitive)
in a retrieved chunk's text. Results go to evals/results/retrieval.json.

Usage:  python evals/run_retrieval.py [evals/golden/aapl.yaml]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml
from app.rag.retriever import rerank, search

RESULTS_DIR = Path("evals/results")
K = 5


def _rank_of_hit(chunks: list[Any], phrases: list[str]) -> int | None:
    """1-based rank of the first chunk containing any expected phrase."""
    for rank, chunk in enumerate(chunks, start=1):
        text = chunk.text.lower()
        if any(p.lower() in text for p in phrases):
            return rank
    return None


def main(argv: list[str]) -> int:
    golden_path = Path(argv[0]) if argv else Path("evals/golden/aapl.yaml")
    golden = yaml.safe_load(golden_path.read_text(encoding="utf-8"))
    ticker = str(golden["ticker"])
    questions = [q for q in golden["questions"] if q.get("expected_phrases")]
    if not questions:
        raise SystemExit(
            f"{golden_path} has no answered questions yet — fill in expected_phrases first."
        )

    rows: list[dict[str, Any]] = []
    for item in questions:
        question = str(item["question"])
        phrases = [str(p) for p in item["expected_phrases"]]

        candidates = search(question, ticker=ticker)
        rank_plain = _rank_of_hit(candidates[:K], phrases)
        reranked = rerank(question, candidates, top_n=K)
        rank_reranked = _rank_of_hit(reranked, phrases)
        rows.append(
            {
                "question": question,
                "rank_dense": rank_plain,
                "rank_reranked": rank_reranked,
            }
        )

    def metrics(key: str) -> tuple[float, float]:
        ranks = [r[key] for r in rows]
        hits = sum(1 for r in ranks if r is not None)
        mrr = sum(1.0 / r for r in ranks if r is not None) / len(ranks)
        return hits / len(ranks), mrr

    hit_dense, mrr_dense = metrics("rank_dense")
    hit_rr, mrr_rr = metrics("rank_reranked")

    print(f"Golden set: {golden_path} ({len(rows)} questions, ticker {ticker})\n")
    print(f"{'pipeline':24s} {'hit-rate@5':>10s} {'MRR':>8s}")
    print(f"{'dense only':24s} {hit_dense:>10.2%} {mrr_dense:>8.3f}")
    print(f"{'dense + rerank':24s} {hit_rr:>10.2%} {mrr_rr:>8.3f}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / "retrieval.json"
    out.write_text(
        json.dumps(
            {
                "golden": str(golden_path),
                "k": K,
                "dense": {"hit_rate": hit_dense, "mrr": mrr_dense},
                "reranked": {"hit_rate": hit_rr, "mrr": mrr_rr},
                "questions": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
