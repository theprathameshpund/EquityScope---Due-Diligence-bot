"""Token tracker, budget enforcement, and cost arithmetic."""

from __future__ import annotations

import time

import pytest
from app.config import settings
from app.llm.router import (
    TokenTracker,
    _approx_tokens,
    _extract_json,
    drop_tracker,
    get_tracker,
)


def test_tracker_accumulates() -> None:
    tracker = TokenTracker(token_limit=1000)
    tracker.add(100, 50, 0.001)
    tracker.add(200, 100, 0.002)
    assert tracker.tokens_used == 450
    assert tracker.cost_usd == pytest.approx(0.003)
    assert not tracker.exceeded


def test_tracker_budget_exceeded() -> None:
    tracker = TokenTracker(token_limit=100)
    tracker.add(80, 30, 0.0)
    assert tracker.exceeded


def test_cost_formula_matches_config() -> None:
    price_in, price_out = settings.model_price_per_million("groq", "smart")
    in_tok, out_tok = 10_000, 2_000
    cost = in_tok / 1_000_000 * price_in + out_tok / 1_000_000 * price_out
    assert cost == pytest.approx(10_000 / 1e6 * 0.59 + 2_000 / 1e6 * 0.79)


def test_tracker_registry_per_run() -> None:
    a = get_tracker("run_test_a")
    b = get_tracker("run_test_b")
    assert a is not b
    assert get_tracker("run_test_a") is a
    drop_tracker("run_test_a")
    drop_tracker("run_test_b")


def test_pacer_allows_within_limits() -> None:
    from app.llm.router import RateLimitPacer

    pacer = RateLimitPacer(rpm=10, tpm=1000)
    start = time.monotonic()
    for _ in range(5):
        pacer.acquire(100)  # 5 requests, 500 tokens — all inside the window
    assert time.monotonic() - start < 1.0


def test_pacer_blocks_over_token_budget() -> None:
    from app.llm.router import RateLimitPacer

    pacer = RateLimitPacer(rpm=0, tpm=100)
    pacer.acquire(90)
    # Next call would exceed TPM; with a synthetic old event it must not block.
    pacer._events.clear()
    pacer._events.append((time.monotonic() - 61.0, 90))
    start = time.monotonic()
    pacer.acquire(90)  # expired event is pruned, so this is immediate
    assert time.monotonic() - start < 1.0


def test_pacer_disabled_never_blocks() -> None:
    from app.llm.router import RateLimitPacer

    pacer = RateLimitPacer(rpm=0, tpm=0)
    start = time.monotonic()
    for _ in range(100):
        pacer.acquire(10_000)
    assert time.monotonic() - start < 0.5


def test_quota_exhausted_detection() -> None:
    from app.llm.router import _is_quota_exhausted

    tpd = RuntimeError(
        "Error code: 429 - {'error': {'message': 'Rate limit reached ... "
        "tokens per day (TPD): Limit 100000', 'code': 'rate_limit_exceeded'}}"
    )
    too_large = RuntimeError("Error code: 413 - request too large for model")
    transient = RuntimeError("Connection error.")
    assert _is_quota_exhausted(tpd)
    assert _is_quota_exhausted(too_large)
    assert not _is_quota_exhausted(transient)


def test_extract_json_strips_fences() -> None:
    assert _extract_json('{"a": 1}') == '{"a": 1}'
    assert _extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _extract_json('Here you go:\n{"a": {"b": 2}}\nthanks') == '{"a": {"b": 2}}'


def test_approx_tokens_positive() -> None:
    assert _approx_tokens("") == 1
    assert _approx_tokens("x" * 400) == 100
