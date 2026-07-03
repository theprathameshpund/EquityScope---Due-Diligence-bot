"""Regression: 413 request-too-large handling in the LLM router.

Observed failure mode: a json_validate_failed retry doubled max_tokens past
the smart tier's 6k TPM ceiling; the resulting 413 was misclassified as quota
exhaustion, rotated pointlessly through every API key (the limit is
structural per org), was retried verbatim by tenacity, and the failed
attempts still filled the rate-pacer window — stalling the run ~1 minute per
attempt.
"""

from __future__ import annotations

from app.llm.router import (
    RateLimitPacer,
    _clamp_max_tokens_to_tpm,
    _is_quota_exhausted,
    _is_request_too_large,
    _is_retryable,
)

_413_MESSAGE = (
    "Error code: 413 - {'error': {'message': 'Request too large for model "
    "`qwen/qwen3-32b` in organization `org_x` service tier `on_demand` on "
    "tokens per minute (TPM): Limit 6000'}}"
)


class APIStatusError(Exception):
    """Same type name as the SDK's — normally retryable."""


def test_413_is_request_too_large_not_quota() -> None:
    exc = APIStatusError(_413_MESSAGE)
    assert _is_request_too_large(exc)
    assert not _is_quota_exhausted(exc)


def test_413_is_never_retried_verbatim() -> None:
    # APIStatusError is in the retryable-type set, but an oversized request
    # fails identically every time — it must be excluded.
    assert _is_retryable(APIStatusError("connection reset"))
    assert not _is_retryable(APIStatusError(_413_MESSAGE))


def test_429_still_counts_as_quota() -> None:
    exc = APIStatusError("Error code: 429 - rate_limit_exceeded")
    assert _is_quota_exhausted(exc)
    assert not _is_request_too_large(exc)


def test_clamp_keeps_request_under_tpm_ceiling() -> None:
    # Smart tier TPM comes from settings (default 5500 in tests). An input of
    # ~4800 tokens must clamp the output so input + output <= TPM.
    from app.config import settings

    tpm = settings.groq_tpm_smart
    input_estimate = 4800
    clamped = _clamp_max_tokens_to_tpm("groq", "smart", input_estimate, 1800)
    assert input_estimate + clamped <= tpm
    assert clamped >= 256  # never starves the response entirely


def test_clamp_noop_when_request_fits() -> None:
    assert _clamp_max_tokens_to_tpm("groq", "fast", 1000, 2000) == 2000
    assert _clamp_max_tokens_to_tpm("anthropic", "smart", 100_000, 4096) == 4096


def test_pacer_refund_frees_the_window() -> None:
    pacer = RateLimitPacer(rpm=10, tpm=6000)
    pacer.acquire(5900)
    pacer.refund(5900)
    # Window is empty again: a second near-limit request must not block.
    import time

    start = time.monotonic()
    pacer.acquire(5900)
    assert time.monotonic() - start < 1.0
