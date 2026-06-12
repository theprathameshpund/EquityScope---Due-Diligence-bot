"""Application configuration.

Single source of truth for every setting and secret in EquityScope.
All values come from environment variables (or `.env`), validated by
pydantic-settings. Nothing elsewhere in the codebase may hardcode a key,
URL, model name, or tuning constant.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import truststore
from pydantic import Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Trust the OS certificate store (corporate TLS-interception proxies install
# their root CA there, but Python's bundled certifi does not include it).
truststore.inject_into_ssl()

# curl_cffi (used by yfinance) bypasses Python's ssl module; point it at a
# PEM bundle of certifi + OS roots when one has been generated (see README).
_CA_BUNDLE = Path(__file__).resolve().parent.parent / "data" / "ca_bundle.pem"
if _CA_BUNDLE.exists():
    os.environ.setdefault("CURL_CA_BUNDLE", str(_CA_BUNDLE))

_REQUIRED_HINTS: dict[str, str] = {
    "GROQ_API_KEY": "create one at https://console.groq.com/keys",
    "EDGAR_USER_AGENT": 'SEC requires a contact string, e.g. "Jane Doe jane@example.com"',
}


# Resolve .env relative to backend/ (parent of the app/ package),
# so `uvicorn app.main:app` works from any working directory.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """All EquityScope configuration, loaded from the environment / `.env`."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── LLM providers ──────────────────────────────────────────
    groq_api_key: str = ""
    groq_model_fast: str = "llama-3.1-8b-instant"
    groq_model_smart: str = "llama-3.3-70b-versatile"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    llm_writer_provider: Literal["groq", "anthropic"] = "groq"
    llm_max_retries: int = Field(default=3, ge=0)
    llm_request_timeout_s: float = Field(default=60.0, gt=0)

    # Proactive client-side pacing so runs never slam into provider 429s.
    # Groq free tier: llama-3.1-8b-instant = 20k TPM / 30 RPM;
    #                  llama-3.3-70b-versatile = 6k TPM / 30 RPM.
    # Setting smart TPM slightly below the cap gives headroom so we never hit 429.
    groq_max_rpm: int = Field(default=28, ge=0)
    groq_tpm_fast: int = Field(default=18_000, ge=0)   # 8b-instant: real limit 20k
    groq_tpm_smart: int = Field(default=5_500, ge=0)   # 70b: real limit 6k

    # ── Embeddings ─────────────────────────────────────────────
    embedding_model: str = "BAAI/bge-large-en-v1.5"
    embedding_device: str = "cpu"

    # ── Vector DB ──────────────────────────────────────────────
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "equityscope_filings"

    # ── Storage ────────────────────────────────────────────────
    postgres_dsn: str = "postgresql://equityscope:equityscope@localhost:5432/equityscope"
    redis_url: str = "redis://localhost:6379/0"

    # ── Data sources ───────────────────────────────────────────
    edgar_user_agent: str = ""
    fred_api_key: str = ""
    news_rss_enabled: bool = True

    # ── Pipeline tuning ────────────────────────────────────────
    chunk_max_tokens: int = Field(default=800, gt=0)
    chunk_overlap_tokens: int = Field(default=100, ge=0)
    retrieval_top_k: int = Field(default=12, gt=0)
    rerank_top_n: int = Field(default=5, gt=0)
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    critic_nli_model: str = "cross-encoder/nli-deberta-v3-base"
    critic_max_revisions: int = Field(default=1, ge=0)   # 2 revision passes add 2+ min on free tier
    critic_entailment_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    run_token_budget: int = Field(default=150_000, gt=0)
    filings_lookback_8k_months: int = Field(default=12, gt=0)
    filings_num_10q: int = Field(default=4, ge=0)

    # ── App ────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    frontend_port: int = 8501
    cors_origins: str = "http://localhost:4200,http://127.0.0.1:4200"
    """Comma-separated allowed origins for the browser frontend."""
    log_level: str = "INFO"
    environment: Literal["development", "production"] = "development"

    # ── Observability (optional) ───────────────────────────────
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # ── Cost table (USD per 1M tokens) ─────────────────────────
    cost_groq_fast_in: float = 0.05
    cost_groq_fast_out: float = 0.08
    cost_groq_smart_in: float = 0.59
    cost_groq_smart_out: float = 0.79
    cost_anthropic_in: float = 3.00
    cost_anthropic_out: float = 15.00

    # ── Paths (derived, not env-driven) ────────────────────────
    filings_cache_dir: Path = Path("data/filings")

    # SEC EDGAR hard limit is 10 req/s; we stay at it, never above.
    edgar_max_requests_per_second: int = Field(default=10, gt=0)

    @model_validator(mode="before")
    @classmethod
    def _strip_inline_comments(cls, data: object) -> object:
        """Drop `value   # comment` tails that dotenv passes through as values."""
        if not isinstance(data, dict):
            return data
        cleaned: dict[object, object] = {}
        for key, value in data.items():
            if isinstance(value, str):
                if value.lstrip().startswith("#"):
                    value = ""
                elif " #" in value:
                    value = value.split(" #", 1)[0].rstrip()
            cleaned[key] = value
        return cleaned

    @model_validator(mode="after")
    def _check_required(self) -> Settings:
        missing: list[str] = []
        if not self.groq_api_key.strip():
            missing.append("GROQ_API_KEY")
        if not self.edgar_user_agent.strip():
            missing.append("EDGAR_USER_AGENT")
        if self.llm_writer_provider == "anthropic" and not self.anthropic_api_key.strip():
            missing.append("ANTHROPIC_API_KEY (required because LLM_WRITER_PROVIDER=anthropic)")
        if missing:
            lines = [
                "EquityScope is missing required configuration:",
                *(
                    f"  - {name}: {_REQUIRED_HINTS.get(name.split(' ')[0], '')}".rstrip(": ")
                    for name in missing
                ),
                "Copy .env.example to .env and fill in the values above.",
            ]
            raise ValueError("\n".join(lines))
        return self

    def model_price_per_million(self, provider: str, model_tier: str) -> tuple[float, float]:
        """Return (input, output) USD price per 1M tokens for a routed model."""
        table = {
            ("groq", "fast"): (self.cost_groq_fast_in, self.cost_groq_fast_out),
            ("groq", "smart"): (self.cost_groq_smart_in, self.cost_groq_smart_out),
            ("anthropic", "smart"): (self.cost_anthropic_in, self.cost_anthropic_out),
        }
        key = (provider, model_tier)
        if key not in table:
            raise KeyError(f"No cost entry configured for provider={provider} tier={model_tier}")
        return table[key]


def load_settings() -> Settings:
    """Instantiate Settings, converting validation noise into a clear message."""
    try:
        return Settings()
    except ValidationError as exc:
        messages: list[str] = []
        for err in exc.errors():
            msg = str(err.get("msg", ""))
            # Strip pydantic's "Value error, " prefix from our own validator output.
            messages.append(msg.removeprefix("Value error, "))
        raise RuntimeError(
            "\n".join(messages)
            if messages
            else "Invalid EquityScope configuration; check your .env file."
        ) from exc


settings: Settings = load_settings()
