"""LLM routing: task tier ? provider/model, retries, token counting, cost.

All LLM calls in EquityScope go through `LLMRouter`. It enforces the
per-run token budget, counts tokens from provider usage data, and prices
each call from the cost table in Settings.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
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

from app.config import settings
from app.logging_setup import get_logger

log = get_logger(__name__)

TaskTier = Literal["fast", "smart", "writer", "critic"]
T = TypeVar("T", bound=BaseModel)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


class BudgetExceededError(RuntimeError):
    """Raised when a run hits RUN_TOKEN_BUDGET."""


class RateLimitPacer:
    """Sliding-window pacing: keeps requests/minute and tokens/minute under
    the provider's published limits so calls wait instead of getting 429s.
    Thread-safe - parallel agent nodes share one pacer per model tier."""

    def __init__(self, rpm: int, tpm: int) -> None:
        self.rpm = rpm
        self.tpm = tpm
        self._events: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def acquire(self, estimated_tokens: int) -> None:
        if self.rpm <= 0 and self.tpm <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                while self._events and now - self._events[0][0] > 60.0:
                    self._events.popleft()
                requests_ok = self.rpm <= 0 or len(self._events) < self.rpm
                tokens_in_window = sum(tokens for _, tokens in self._events)
                # If the window is empty and a single call exceeds TPM, allow it
                # through anyway - we can't split one LLM request across windows.
                window_empty = len(self._events) == 0
                tokens_ok = (
                    self.tpm <= 0
                    or window_empty
                    or tokens_in_window + estimated_tokens <= self.tpm
                )
                if requests_ok and tokens_ok:
                    self._events.append((now, estimated_tokens))
                    return
                wait = (self._events[0][0] + 60.0 - now) if self._events else 1.0
            wait = min(max(wait, 0.5), 61.0)
            log.info(
                "llm_rate_pacing",
                wait_s=round(wait, 1),
                window_tokens=tokens_in_window,
                window_requests=len(self._events),
            )
            time.sleep(wait)

    def refund(self, estimated_tokens: int) -> None:
        """Remove a reserved slot after the provider rejected the request
        outright (413 request-too-large) — no tokens were actually consumed,
        so the failed attempt must not stall the next one for a minute."""
        if self.rpm <= 0 and self.tpm <= 0:
            return
        with self._lock:
            for i in range(len(self._events) - 1, -1, -1):
                if self._events[i][1] == estimated_tokens:
                    del self._events[i]
                    return


_pacers: dict[tuple[str, str], RateLimitPacer] = {}
_pacers_lock = threading.Lock()
_groq_key_index = 0
_groq_key_lock = threading.Lock()


def _get_pacer(provider: str, price_tier: str) -> RateLimitPacer:
    key = (provider, price_tier)
    with _pacers_lock:
        if key not in _pacers:
            if provider == "groq":
                tpm = settings.groq_tpm_fast if price_tier == "fast" else settings.groq_tpm_smart
                _pacers[key] = RateLimitPacer(rpm=settings.groq_max_rpm, tpm=tpm)
            else:
                # Anthropic paid tiers are generous; tenacity retries suffice.
                _pacers[key] = RateLimitPacer(rpm=0, tpm=0)
        return _pacers[key]


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


def _is_quota_exhausted(exc: BaseException) -> bool:
    """Provider quota errors that retrying cannot fix inside one run -
    daily/minute token caps. Rotating to another API key CAN help here."""
    message = str(exc)
    return "rate_limit_exceeded" in message or "Error code: 429" in message


def _is_request_too_large(exc: BaseException) -> bool:
    """A single request that exceeds the model tier's structural per-request
    ceiling (413). Every org has the same limit, so key rotation can never
    fix it — the request must be shrunk or routed to a bigger-TPM tier."""
    message = str(exc)
    return "Error code: 413" in message or "Request too large" in message


def _quota_cooldown_s(exc: BaseException) -> float:
    """How long to route smart-tier calls straight to the fast model after
    this quota error, skipping pointless probes of an exhausted quota."""
    message = str(exc)
    if "per day" in message or "TPD" in message:
        return 600.0
    if "per minute" in message or "TPM" in message:
        return 90.0
    return 0.0


_smart_fallback_until = 0.0
_smart_fallback_lock = threading.Lock()


def _is_retryable(exc: BaseException) -> bool:
    # Oversized requests fail identically on every retry — never retry them
    # verbatim; the caller must shrink the request or change tier instead.
    if _is_request_too_large(exc):
        return False
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


def _tier_tpm(provider: str, price_tier: str) -> int:
    if provider != "groq":
        return 0
    return settings.groq_tpm_fast if price_tier == "fast" else settings.groq_tpm_smart


def _clamp_max_tokens_to_tpm(
    provider: str, price_tier: str, input_estimate: int, max_tokens: int
) -> int:
    """Cap the output budget so input + output fits the tier's per-minute
    token ceiling. Without this, retry logic that doubles max_tokens produces
    requests that 413 on every key (the limit is structural per org)."""
    tpm = _tier_tpm(provider, price_tier)
    if tpm <= 0:
        return max_tokens
    headroom = tpm - input_estimate - 64  # small margin for provider-side counting
    if headroom >= max_tokens:
        return max_tokens
    clamped = max(256, headroom)
    if clamped < max_tokens:
        log.info(
            "llm_max_tokens_clamped_to_tpm",
            tier=price_tier,
            tpm=tpm,
            input_estimate=input_estimate,
            requested=max_tokens,
            clamped=clamped,
        )
    return min(max_tokens, clamped)


def _route(tier: TaskTier) -> tuple[str, str, str]:
    """tier ? (provider, model, price_tier)."""
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

    # -- provider clients (lazy) ---------------------

    @staticmethod
    @lru_cache(maxsize=3)
    def _groq_client(api_key: str) -> object:
        from groq import Groq

        return Groq(api_key=api_key, timeout=settings.llm_request_timeout_s)

    @staticmethod
    @lru_cache(maxsize=1)
    def _anthropic_client() -> object:
        from anthropic import Anthropic

        return Anthropic(
            api_key=settings.anthropic_api_key, timeout=settings.llm_request_timeout_s
        )

    # -- core call ------------------------------

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
        global _smart_fallback_until
        provider, model, price_tier = _route(tier)
        # During a quota cooldown, don't even probe the exhausted smart model.
        if provider == "groq" and model != settings.groq_model_fast:
            with _smart_fallback_lock:
                if time.monotonic() < _smart_fallback_until:
                    provider, model, price_tier = "groq", settings.groq_model_fast, "fast"
        # Clamp output so a single request always fits the tier's TPM ceiling,
        # then estimate conservatively: full prompt plus the output allowance.
        input_estimate = _approx_tokens(system + user)
        max_tokens = _clamp_max_tokens_to_tpm(provider, price_tier, input_estimate, max_tokens)
        estimated_tokens = input_estimate + max_tokens

        def _paced_call(prov: str, mdl: str, tier_name: str, out_budget: int) -> tuple[str, int, int]:
            @retry(
                stop=stop_after_attempt(settings.llm_max_retries + 1),
                wait=wait_exponential(multiplier=1, max=20),
                retry=retry_if_exception(_is_retryable),
                reraise=True,
            )
            def _call() -> tuple[str, int, int]:
                pacer = _get_pacer(prov, tier_name)
                pacer.acquire(estimated_tokens)
                try:
                    if prov == "anthropic":
                        return self._call_anthropic(mdl, system, user, out_budget, temperature)
                    return self._call_groq(mdl, system, user, json_mode, out_budget, temperature)
                except Exception as exc:
                    if _is_request_too_large(exc):
                        # The provider rejected it outright — nothing was
                        # consumed, so free the pacer slot immediately.
                        pacer.refund(estimated_tokens)
                    raise

            return _call()

        try:
            text, in_tok, out_tok = _paced_call(provider, model, price_tier, max_tokens)
        except Exception as exc:
            # Two rescuable cases when the smart Groq model fails:
            #  - quota exhaustion (429/TPD): the fast model has a separate,
            #    much larger quota;
            #  - request too large (413): the fast tier's TPM ceiling is ~3x
            #    higher, so the same request usually fits there.
            can_fall_back = (
                provider == "groq"
                and model != settings.groq_model_fast
                and (_is_quota_exhausted(exc) or _is_request_too_large(exc))
            )
            if not can_fall_back:
                raise
            cooldown = _quota_cooldown_s(exc)
            if cooldown > 0:
                with _smart_fallback_lock:
                    _smart_fallback_until = max(
                        _smart_fallback_until, time.monotonic() + cooldown
                    )
            log.warning(
                "smart_model_quota_exhausted_falling_back_to_fast",
                tier=tier,
                model=model,
                cooldown_s=cooldown,
                reason="request_too_large" if _is_request_too_large(exc) else "quota",
                error=str(exc)[:200],
            )
            provider, model, price_tier = "groq", settings.groq_model_fast, "fast"
            # Re-clamp for the fast tier's (larger) TPM ceiling.
            fallback_max_tokens = _clamp_max_tokens_to_tpm(
                provider, price_tier, input_estimate, max_tokens
            )
            text, in_tok, out_tok = _paced_call(provider, model, price_tier, fallback_max_tokens)
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

        global _groq_key_index
        keys = settings.groq_api_keys
        last_quota_error: BaseException | None = None
        with _groq_key_lock:
            start_index = _groq_key_index % len(keys)

        for offset in range(len(keys)):
            key_index = (start_index + offset) % len(keys)
            client = self._groq_client(keys[key_index])
            assert isinstance(client, Groq)
            try:
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
            except Exception as exc:
                if not _is_quota_exhausted(exc) or offset == len(keys) - 1:
                    raise
                last_quota_error = exc
                with _groq_key_lock:
                    if _groq_key_index % len(keys) == key_index:
                        _groq_key_index = (key_index + 1) % len(keys)
                log.warning(
                    "groq_api_key_quota_exhausted_trying_next",
                    key_index=key_index + 1,
                    next_key_index=((key_index + 1) % len(keys)) + 1,
                    error=str(exc)[:200],
                )
                continue
            text = response.choices[0].message.content or ""
            usage = response.usage
            in_tok = usage.prompt_tokens if usage else _approx_tokens(system + user)
            out_tok = usage.completion_tokens if usage else _approx_tokens(text)
            return text, in_tok, out_tok

        assert last_quota_error is not None
        raise last_quota_error

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

    # -- structured output -------------------------

    def complete_json(
        self,
        tier: TaskTier,
        system: str,
        user: str,
        schema: type[T],
        *,
        max_tokens: int = 1024,
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
            try:
                response = self.complete(
                    tier,
                    system_full,
                    prompt,
                    json_mode=True,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            except Exception as exc:
                # If provider rejects the request due to size (413 Payload Too Large),
                # try again with a reduced prompt: omit the full JSON Schema and ask
                # for JSON matching the schema (safer for large schemas).
                msg = str(exc)
                log.info("llm_call_failed", attempt=attempt, error=msg[:200])
                if "413" in msg or "Payload Too Large" in msg:
                    log.warning("llm_payload_too_large", attempt=attempt)
                    # First fallback: reduce input pressure and remove the full schema from the prompt.
                    max_tokens = max(128, max_tokens // 2)
                    system_full = (
                        f"{system}\n\nRespond ONLY with a JSON object matching the required schema (no markdown fences, no commentary)."
                    )
                    last_error = "413 Payload Too Large"
                    continue
                json_mode_failure = "json_validate_failed" in msg or "Failed to generate JSON" in msg
                if json_mode_failure and attempt < settings.llm_max_retries:
                    token_limited = "max completion tokens" in msg
                    new_max_tokens = (
                        min(max(max_tokens * 2, max_tokens + 512), 4096)
                        if token_limited
                        else max_tokens
                    )
                    log.warning(
                        "llm_json_generation_retry",
                        attempt=attempt,
                        old_max_tokens=max_tokens,
                        new_max_tokens=new_max_tokens,
                        token_limited=token_limited,
                    )
                    max_tokens = new_max_tokens
                    system_full = (
                        f"{system}\n\nRespond ONLY with one valid JSON object matching the required schema. "
                        "The first character must be { and the last character must be }. "
                        "Do not return a bare array, markdown fences, commentary, or extra text."
                    )
                    last_error = "provider rejected invalid JSON object"
                    continue
                raise
            try:
                payload = _extract_json(response.text)
                try:
                    return schema.model_validate_json(payload)
                except ValidationError:
                    # Some providers occasionally wrap the schema object as
                    # {"<SchemaName>": {...}}; unwrap it to avoid retries.
                    parsed = json.loads(payload)
                    if isinstance(parsed, dict):
                        for key in (schema.__name__, schema.__name__.lstrip("_")):
                            nested = parsed.get(key)
                            if isinstance(nested, dict):
                                return schema.model_validate(nested)
                    raise
            except (ValidationError, json.JSONDecodeError) as exc:
                last_error = str(exc)[:800]
                log.warning("structured_output_invalid", attempt=attempt, error=last_error[:200])
        raise ValueError(f"LLM failed to produce valid {schema.__name__}: {last_error[:300]}")


def _extract_json(text: str) -> str:
    """Strip markdown fences / pre-amble around a JSON object."""
    stripped = text.strip()
    # Fast path: starts with a JSON object
    if stripped.startswith("{"):
        # Find the first balanced JSON object to avoid greedy regex issues
        depth = 0
        start = None
        for i, ch in enumerate(stripped):
            if ch == "{" and start is None:
                start = i
                depth = 1
                continue
            if start is not None:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return stripped[start : i + 1]
        # Fallback to full stripped text if we couldn't balance braces
        return stripped
    # Otherwise try to find first `{` and extract balanced object
    first = stripped.find("{")
    if first == -1:
        return stripped
    depth = 0
    start = None
    for i in range(first, len(stripped)):
        ch = stripped[i]
        if ch == "{" and start is None:
            start = i
            depth = 1
            continue
        if start is not None:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return stripped[start : i + 1]
    return stripped


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)
