"""Config validation: fail-fast with helpful messages, cost table."""

from __future__ import annotations

import pytest
from src.config import Settings


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GROQ_API_KEY", "EDGAR_USER_AGENT", "ANTHROPIC_API_KEY",
                "LLM_WRITER_PROVIDER"):
        monkeypatch.delenv(var, raising=False)


def test_missing_required_vars_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    with pytest.raises(Exception) as excinfo:
        Settings(_env_file=None)
    message = str(excinfo.value)
    assert "GROQ_API_KEY" in message
    assert "EDGAR_USER_AGENT" in message
    assert ".env" in message


def test_anthropic_key_required_when_provider_anthropic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean_env(monkeypatch)
    with pytest.raises(Exception) as excinfo:
        Settings(
            _env_file=None,
            groq_api_key="x",
            edgar_user_agent="Test test@example.com",
            llm_writer_provider="anthropic",
        )
    assert "ANTHROPIC_API_KEY" in str(excinfo.value)


def test_valid_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    settings = Settings(
        _env_file=None,
        groq_api_key="x",
        edgar_user_agent="Test test@example.com",
    )
    assert settings.chunk_max_tokens == 800
    assert settings.critic_entailment_threshold == 0.7


def test_inline_env_comments_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    settings = Settings(
        _env_file=None,
        groq_api_key="x",
        edgar_user_agent="Test test@example.com",
        fred_api_key="# optional — macro context section skipped if empty",
        news_rss_enabled="true            # Google News RSS, no key needed",  # type: ignore[arg-type]
    )
    # A value that is only a comment becomes empty; trailing comments are cut.
    assert settings.fred_api_key == ""
    assert settings.news_rss_enabled is True


def test_cost_table_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    settings = Settings(
        _env_file=None,
        groq_api_key="x",
        edgar_user_agent="Test test@example.com",
    )
    assert settings.model_price_per_million("groq", "fast") == (0.05, 0.08)
    assert settings.model_price_per_million("anthropic", "smart") == (3.00, 15.00)
    with pytest.raises(KeyError):
        settings.model_price_per_million("groq", "huge")
