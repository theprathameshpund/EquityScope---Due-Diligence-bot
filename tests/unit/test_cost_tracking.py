"""Token tracker, budget enforcement, and cost arithmetic."""

from __future__ import annotations

import pytest
from src.config import settings
from src.llm.router import (
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


def test_extract_json_strips_fences() -> None:
    assert _extract_json('{"a": 1}') == '{"a": 1}'
    assert _extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _extract_json('Here you go:\n{"a": {"b": 2}}\nthanks') == '{"a": {"b": 2}}'


def test_approx_tokens_positive() -> None:
    assert _approx_tokens("") == 1
    assert _approx_tokens("x" * 400) == 100
