"""Google News RSS via feedparser. Headlines + snippets only, no scraping."""

from __future__ import annotations

import urllib.parse
from datetime import UTC, datetime, timedelta
from time import mktime
from typing import Any

from app.config import settings
from app.logging_setup import get_logger
from app.state import NewsItem

log = get_logger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

NEWS_LOOKBACK_DAYS = 90
NEWS_MAX_ITEMS = 15


def fetch_news(company_name: str, ticker: str) -> list[NewsItem]:
    """Top recent Google News items for the company (last 90 days, max 15)."""
    if not settings.news_rss_enabled:
        return []
    import feedparser

    query = urllib.parse.quote(f'"{company_name}" OR {ticker} stock')
    url = GOOGLE_NEWS_RSS.format(query=query)
    feed: Any = feedparser.parse(url)
    if getattr(feed, "bozo", False) and not feed.entries:
        raise RuntimeError(f"News feed unavailable: {getattr(feed, 'bozo_exception', 'unknown')}")

    cutoff = datetime.now(UTC) - timedelta(days=NEWS_LOOKBACK_DAYS)
    items: list[NewsItem] = []
    for entry in feed.entries:
        published: datetime | None = None
        parsed_time = getattr(entry, "published_parsed", None)
        if parsed_time is not None:
            published = datetime.fromtimestamp(mktime(parsed_time), tz=UTC)
            if published < cutoff:
                continue
        source = ""
        source_obj = getattr(entry, "source", None)
        if source_obj is not None:
            source = str(getattr(source_obj, "title", ""))
        items.append(
            NewsItem(
                title=str(getattr(entry, "title", "")),
                source=source,
                published_at=published,
                url=str(getattr(entry, "link", "")),
                snippet=str(getattr(entry, "summary", ""))[:500],
            )
        )
        if len(items) >= NEWS_MAX_ITEMS:
            break
    log.info("news_fetched", company=company_name, items=len(items))
    return items
