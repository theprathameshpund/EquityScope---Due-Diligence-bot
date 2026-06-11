"""NLI-based claim-vs-chunk entailment scoring for the critic agent."""

from __future__ import annotations

import math
from typing import cast

from src.config import settings
from src.logging_setup import get_logger
from src.rag.embeddings import get_cross_encoder

log = get_logger(__name__)

# Label order for cross-encoder/nli-deberta-v3-base: contradiction, entailment, neutral
_ENTAILMENT_INDEX = 1


def _softmax(logits: list[float]) -> list[float]:
    peak = max(logits)
    exps = [math.exp(x - peak) for x in logits]
    total = sum(exps)
    return [e / total for e in exps]


def entailment_score(claim: str, premise: str) -> float:
    """P(entailment) that the premise (source chunk) supports the claim."""
    model = get_cross_encoder(settings.critic_nli_model)
    logits = model.predict([(premise, claim)], show_progress_bar=False)
    row = cast("list[float]", logits[0].tolist())
    if len(row) < 3:  # binary relevance model fallback
        return float(row[0])
    return _softmax(row)[_ENTAILMENT_INDEX]


def best_entailment(claim: str, premises: list[str]) -> float:
    """Highest entailment probability across all cited chunks."""
    if not premises:
        return 0.0
    model = get_cross_encoder(settings.critic_nli_model)
    logits = model.predict([(p, claim) for p in premises], show_progress_bar=False)
    best = 0.0
    for raw in logits.tolist():
        row = cast("list[float]", raw)
        score = float(row[0]) if len(row) < 3 else _softmax(row)[_ENTAILMENT_INDEX]
        best = max(best, score)
    return best


def is_grounded(claim: str, premises: list[str]) -> tuple[bool, float]:
    score = best_entailment(claim, premises)
    return score >= settings.critic_entailment_threshold, score
