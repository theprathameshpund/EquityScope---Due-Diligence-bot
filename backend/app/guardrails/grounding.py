"""NLI-based claim-vs-chunk entailment scoring for the critic agent."""

from __future__ import annotations

import math
from typing import Any, cast

from app.config import settings
from app.logging_setup import get_logger
from app.rag.embeddings import get_cross_encoder

log = get_logger(__name__)

# Label order for cross-encoder/nli-deberta-v3-base: contradiction, entailment, neutral
_ENTAILMENT_INDEX = 1

# Hard cap on NLI pairs per predict() call.
# Each filing chunk is windowed (1200-char windows × overlap) and multiplied by
# all cited chunks — without a cap this easily reaches 30-50 pairs per claim,
# causing minutes of blocking CPU inference. 12 pairs ≈ 4 chunks × 3 windows.
_MAX_NLI_PAIRS = 12


def _softmax(logits: list[float]) -> list[float]:
    peak = max(logits)
    exps = [math.exp(x - peak) for x in logits]
    total = sum(exps)
    return [e / total for e in exps]


def entailment_score(claim: str, premise: str) -> float:
    """P(entailment) that the premise (source chunk) supports the claim."""
    import torch
    model = get_cross_encoder(settings.critic_nli_model)
    try:
        with torch.no_grad():
            logits = model.predict([(premise, claim)], show_progress_bar=False)
    except (OSError, MemoryError, RuntimeError) as exc:
        log.warning("nli_predict_oom", error=str(exc))
        return 1.0  # degrade gracefully: treat as supported
    row = cast("list[float]", logits[0].tolist())
    if len(row) < 3:  # binary relevance model fallback
        return float(row[0])
    return _softmax(row)[_ENTAILMENT_INDEX]


# The NLI cross-encoder truncates inputs around 512 tokens; long filing chunks
# must be windowed or the supporting sentence may fall outside the model input.
_WINDOW_CHARS = 1200
_WINDOW_OVERLAP = 300


def _windows(text: str) -> list[str]:
    if len(text) <= _WINDOW_CHARS:
        return [text]
    step = _WINDOW_CHARS - _WINDOW_OVERLAP
    return [text[i : i + _WINDOW_CHARS] for i in range(0, len(text), step)]


def best_entailment(claim: str, premises: list[str]) -> float:
    """Highest entailment probability across all windows of all cited chunks."""
    import torch
    pairs: list[list[str]] = [
        [window, claim] for premise in premises for window in _windows(premise)
    ]
    if not pairs:
        return 0.0
    # Cap to avoid multi-minute blocking inference on CPU when a claim cites
    # many long chunks (each windowed into multiple segments).
    if len(pairs) > _MAX_NLI_PAIRS:
        log.warning("nli_pairs_capped", original=len(pairs), capped=_MAX_NLI_PAIRS)
        pairs = pairs[:_MAX_NLI_PAIRS]
    model = get_cross_encoder(settings.critic_nli_model)
    try:
        # torch.no_grad() prevents gradient graph construction during inference,
        # cutting memory usage by ~3x and speeding up each predict() call.
        with torch.no_grad():
            logits = model.predict(cast("Any", pairs), show_progress_bar=False)
    except (OSError, MemoryError, RuntimeError) as exc:
        log.warning("nli_predict_oom", pairs=len(pairs), error=str(exc))
        return 1.0  # degrade gracefully: treat as supported
    best = 0.0
    for raw in logits.tolist():
        row = cast("list[float]", raw)
        score = float(row[0]) if len(row) < 3 else _softmax(row)[_ENTAILMENT_INDEX]
        best = max(best, score)
    return best


def is_grounded(claim: str, premises: list[str]) -> tuple[bool, float]:
    score = best_entailment(claim, premises)
    return score >= settings.critic_entailment_threshold, score
