"""News agent: Google News RSS → injection filter → batched sentiment."""

from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel

from src.config import settings
from src.guardrails.injection_filter import sanitize
from src.llm.router import LLMRouter, load_prompt
from src.logging_setup import get_logger
from src.state import AgentState, NewsDigest, SentimentLabel
from src.tools.news import fetch_news

log = get_logger(__name__)

_VALID_LABELS = {"positive", "negative", "neutral"}


class _Sentiments(BaseModel):
    labels: list[str]


def news_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: fetch + sanitize + classify news; degrade gracefully."""
    if not settings.news_rss_enabled:
        return {
            "news": NewsDigest(available=False, error="News RSS disabled by configuration."),
            "data_gaps": ["News disabled (NEWS_RSS_ENABLED=false)."],
        }
    try:
        items = fetch_news(state.company_name, state.ticker)
    except Exception as exc:
        log.warning("news_unavailable", error=str(exc))
        return {
            "news": NewsDigest(available=False, error=str(exc)),
            "data_gaps": [f"News unavailable: {exc}"],
        }

    # Untrusted web text never reaches a prompt unsanitized.
    for item in items:
        item.title = sanitize(item.title, source="news_title")
        item.snippet = sanitize(item.snippet, source="news_snippet")

    if items:
        try:
            router = LLMRouter(state.run_id)
            numbered = "\n".join(
                f"[{i + 1}] {item.title} — {item.snippet[:200]}"
                for i, item in enumerate(items)
            )
            system = load_prompt("news_sentiment").format(company=state.company_name)
            result = router.complete_json(
                "fast", system, numbered, _Sentiments, max_tokens=200
            )
            for i, item in enumerate(items):
                if i < len(result.labels):
                    label = result.labels[i].strip().lower()
                    if label in _VALID_LABELS:
                        item.sentiment = cast("SentimentLabel", label)
        except Exception as exc:
            log.warning("news_sentiment_failed", error=str(exc))

    return {"news": NewsDigest(available=True, items=items)}
