"""
RSS news aggregation — free, no API key required.
Uses feedparser to pull headlines from Yahoo Finance, Google News,
and (for crypto) CoinDesk.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

import feedparser

from backend.app.models.schemas import NewsItem
from backend.app.services import circuit_breaker, rate_limiter
from backend.app.services.cache import cache
from backend.app.services.observability import observe

logger = logging.getLogger(__name__)
_SOURCE = "rss"

# Per-ticker feed URLs. {ticker} is substituted at call time.
_STOCK_FEEDS = [
    "https://finance.yahoo.com/rss/headline?s={ticker}",
    "https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en",
]

_CRYPTO_FEEDS = [
    "https://finance.yahoo.com/rss/headline?s={ticker}",
    "https://news.google.com/rss/search?q={ticker}+crypto&hl=en-US&gl=US&ceid=US:en",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
]


def _parse_feed(url: str) -> list[dict]:
    """Parse one RSS feed. Returns raw entry dicts."""
    cb = circuit_breaker.get(_SOURCE)
    rate_limiter.acquire(_SOURCE)
    try:
        with cb, observe(_SOURCE, "fetch", ticker=url[:60]):
            parsed = feedparser.parse(url)
            return list(parsed.entries)
    except Exception as exc:
        logger.debug("RSS feed failed %s: %s", url[:80], exc)
        return []


def _entry_to_item(entry: dict, source_label: str) -> NewsItem | None:
    """Convert a feedparser entry to NewsItem. Returns None if title is missing."""
    title = entry.get("title", "").strip()
    if not title:
        return None

    url = entry.get("link") or entry.get("url") or ""

    # Parse published date
    pub: datetime = datetime.now(UTC)
    for field in ("published_parsed", "updated_parsed"):
        t = entry.get(field)
        if t:
            try:
                pub = datetime(*t[:6], tzinfo=UTC)
                break
            except Exception:
                pass

    summary = entry.get("summary", "") or entry.get("description", "") or ""
    # Strip HTML tags from summary (feedparser sometimes includes raw HTML)
    import re
    summary = re.sub(r"<[^>]+>", "", summary).strip()[:500]

    return NewsItem(
        title=title,
        url=url,
        publisher=source_label,
        published_at=pub,
        summary=summary,
    )


def fetch_news(ticker: str) -> list[NewsItem]:
    """
    Fetch news via RSS for the given ticker.
    Returns up to 15 items, sorted newest first, deduplicated by title.
    """
    cache_key = f"rss:{ticker.upper()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    is_crypto = ticker.upper().endswith("-USD") or ticker.upper().endswith("-USDT")
    feeds = _CRYPTO_FEEDS if is_crypto else _STOCK_FEEDS

    # Strip the exchange suffix for a cleaner search query
    query = ticker.upper().replace("-USD", "").replace("-USDT", "")

    items: list[NewsItem] = []
    seen_titles: set[str] = set()

    for feed_template in feeds:
        url = feed_template.format(ticker=query)
        entries = _parse_feed(url)
        source_label = _source_label(url)

        for entry in entries[:10]:
            item = _entry_to_item(entry, source_label)
            if item is None:
                continue
            norm_title = item.title.lower()[:80]
            if norm_title in seen_titles:
                continue
            seen_titles.add(norm_title)
            items.append(item)

    items.sort(key=lambda x: x.published_at, reverse=True)
    items = items[:15]

    cache.set(cache_key, items, 300)  # 5 min cache
    return items


def _source_label(url: str) -> str:
    if "yahoo" in url:
        return "Yahoo Finance"
    if "google" in url:
        return "Google News"
    if "coindesk" in url:
        return "CoinDesk"
    if "reuters" in url:
        return "Reuters"
    return "RSS"
