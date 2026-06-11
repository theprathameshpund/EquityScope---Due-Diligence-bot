"""LLM routing: task tier → provider/model, retries, token counting, cost.

All LLM calls in EquityScope go through `LLMRouter`. It enforces the
per-run token budget, counts tokens from provider usage data, and prices
each call from the cost table in Settings.
"""

from __future__ import annotations

import json
import re
import threading
from functools import lru_cache
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ValidationError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from src.config import settings
from src.logging_setup import get_logger

log = get_logger(__name__)

TaskTier = Literal["fast", "smart", "writer", "critic"]
T = TypeVar("T", bound=BaseModel)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


class BudgetExceededError(RuntimeError):
    """Raised when a run hits RUN_TOKEN_BUDGET."""


class LLMResponse(BaseModel):
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    provider: str
    model: str


class TokenTracker:
    """Per-run token/cost accumulator, shared across all agents (thread-safe)."""

    def __init__(self, token_limit: int) -> None:
        self.token_limit = token_limit
        self.tokens_used = 0
        self.cost_usd = 0.0
        self._lock = threading.Lock()

    def add(self, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        with self._lock:
            self.tokens_used += input_tokens + output_tokens
            self.cost_usd += cost_usd

    @property
    def exceeded(self) -> bool:
        return self.tokens_used > self.token_limit


_trackers: dict[str, TokenTracker] = {}
_trackers_lock = threading.Lock()


def get_tracker(run_id: str) -> TokenTracker:
    with _trackers_lock:
        if run_id not in _trackers:
            _trackers[run_id] = TokenTracker(settings.run_token_budget)
        return _trackers[run_id]


def drop_tracker(run_id: str) -> None:
    with _trackers_lock:
        _trackers.pop(run_id, None)


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    """Load an agent prompt from src/llm/prompts/<name>.md."""
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file missing: {path}")
    return path.read_text(encoding="utf-8")


def _is_retryable(exc: BaseException) -> bool:
    name = type(exc).__name__
    return name in {
        "RateLimitError",
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
        "APIStatusError",
        "ConnectError",
        "ReadTimeout",
        "ServiceUnavailableError",
        "OverloadedError",
    }


def _route(tier: TaskTier) -> tuple[str, str, str]:
    """tier → (provider, model, price_tier)."""
    if tier == "fast":
        return "groq", settings.groq_model_fast, "fast"
    if tier == "smart":
        return "groq", settings.groq_model_smart, "smart"
    # writer / critic follow LLM_WRITER_PROVIDER
    if settings.llm_writer_provider == "anthropic":
        return "anthropic", settings.anthropic_model, "smart"
    return "groq", settings.groq_model_smart, "smart"


class LLMRouter:
    """Provider-agnostic chat completion with budget + cost accounting."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.tracker = get_tracker(run_id)

    # ── provider clients (lazy) ───────────────────────────────

    @staticmethod
    @lru_cache(maxsize=1)
    def _groq_client() -> object:
        from groq import Groq

        return Groq(api_key=settings.groq_api_key, timeout=settings.llm_request_timeout_s)

    @staticmethod
    @lru_cache(maxsize=1)
    def _anthropic_client() -> object:
        from anthropic import Anthropic

        return Anthropic(
            api_key=settings.anthropic_api_key, timeout=settings.llm_request_timeout_s
        )

    # ── core call ─────────────────────────────────────────────

    def complete(
        self,
        tier: TaskTier,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> LLMResponse:
        if self.tracker.exceeded:
            raise BudgetExceededError(
                f"Run {self.run_id} exceeded token budget "
                f"({self.tracker.tokens_used}/{self.tracker.token_limit})"
            )
        provider, model, price_tier = _route(tier)

        @retry(
            stop=stop_after_attempt(settings.llm_max_retries + 1),
            wait=wait_exponential(multiplier=1, max=20),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        )
        def _call() -> tuple[str, int, int]:
            if provider == "anthropic":
                return self._call_anthropic(model, system, user, max_tokens, temperature)
            return self._call_groq(model, system, user, json_mode, max_tokens, temperature)

        text, in_tok, out_tok = _call()
        price_in, price_out = settings.model_price_per_million(provider, price_tier)
        cost = in_tok / 1_000_000 * price_in + out_tok / 1_000_000 * price_out
        self.tracker.add(in_tok, out_tok, cost)
        log.info(
            "llm_call",
            tier=tier,
            provider=provider,
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=round(cost, 6),
            run_tokens=self.tracker.tokens_used,
        )
        return LLMResponse(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
            provider=provider,
            model=model,
        )

    def _call_groq(
        self,
        model: str,
        system: str,
        user: str,
        json_mode: bool,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, int, int]:
        from groq import Groq

        client = self._groq_client()
        assert isinstance(client, Groq)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            response_format={"type": "json_object"} if json_mode else None,
        )
        text = response.choices[0].message.content or ""
        usage = response.usage
        in_tok = usage.prompt_tokens if usage else _approx_tokens(system + user)
        out_tok = usage.completion_tokens if usage else _approx_tokens(text)
        return text, in_tok, out_tok

    def _call_anthropic(
        self, model: str, system: str, user: str, max_tokens: int, temperature: float
    ) -> tuple[str, int, int]:
        from anthropic import Anthropic
        from anthropic.types import TextBlock

        client = self._anthropic_client()
        assert isinstance(client, Anthropic)
        response = client.messages.create(
            model=model,
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = "".join(
            block.text for block in response.content if isinstance(block, TextBlock)
        )
        return text, response.usage.input_tokens, response.usage.output_tokens

    # ── structured output ─────────────────────────────────────

    def complete_json(
        self,
        tier: TaskTier,
        system: str,
        user: str,
        schema: type[T],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> T:
        """Completion parsed into a Pydantic model, with validation retries."""
        schema_json = json.dumps(schema.model_json_schema(), indent=None)
        system_full = (
            f"{system}\n\nRespond ONLY with a JSON object matching this JSON Schema "
            f"(no markdown fences, no commentary):\n{schema_json}"
        )
        last_error = ""
        for attempt in range(settings.llm_max_retries + 1):
            prompt = user if not last_error else (
                f"{user}\n\nYour previous response was invalid: {last_error}\n"
                "Return corrected JSON only."
            )
            response = self.complete(
                tier,
                system_full,
                prompt,
                json_mode=True,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            try:
                return schema.model_validate_json(_extract_json(response.text))
            except (ValidationError, json.JSONDecodeError) as exc:
                last_error = str(exc)[:800]
                log.warning("structured_output_invalid", attempt=attempt, error=last_error[:200])
        raise ValueError(f"LLM failed to produce valid {schema.__name__}: {last_error[:300]}")


def _extract_json(text: str) -> str:
    """Strip markdown fences / pre-amble around a JSON object."""
    stripped = text.strip()
    if stripped.startswith("{"):
        return stripped
    match = re.search(r"\{.*\}", stripped, re.DOTALL)
    return match.group(0) if match else stripped


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)
