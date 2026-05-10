"""
News aggregation — pulls from multiple sources, deduplicates, and returns
a fully-enriched EnrichedNewsFeed (rolling windows, momentum, source weighting).

Sources (in priority order):
  1. yfinance news (no key, fast)
  2. RSS feeds via feedparser (no key, Yahoo Finance / Google News / CoinDesk)
  3. NewsAPI free tier (optional, requires NEWS_API_KEY, 100 req/day limit)
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from backend.app.config import get_settings
from backend.app.models.schemas import EnrichedNewsFeed, NewsItem
from backend.app.services import rate_limiter, yfinance_client
from backend.app.services.observability import observe
from backend.app.services.sentiment import analyze_news, build_enriched_feed
from backend.app.services.sources import rss

logger = logging.getLogger(__name__)
_NEWSAPI_BASE = "https://newsapi.org/v2/everything"


def _fetch_newsapi(ticker: str) -> list[NewsItem]:
    settings = get_settings()
    if not settings.has_news_api_key:
        return []

    query = ticker.upper().replace("-USD", "").replace("-USDT", "")
    rate_limiter.acquire("newsapi")

    try:
        with observe("newsapi", "everything", ticker=ticker):
            with httpx.Client(timeout=8.0) as client:
                r = client.get(
                    _NEWSAPI_BASE,
                    params={
                        "q": query,
                        "language": "en",
                        "sortBy": "publishedAt",
                        "pageSize": 10,
                        "apiKey": settings.news_api_key,
                    },
                )
                r.raise_for_status()
                data = r.json()
    except Exception as exc:
        logger.warning("NewsAPI fetch failed for %s: %s", ticker, exc)
        return []

    items: list[NewsItem] = []
    for article in data.get("articles", []):
        try:
            pub_str = article.get("publishedAt", "")
            pub_dt = (
                datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                if pub_str
                else datetime.now(UTC)
            )
            title = article.get("title") or ""
            if not title:
                continue
            items.append(
                NewsItem(
                    title=title,
                    url=article.get("url") or "",
                    publisher=article.get("source", {}).get("name") or "NewsAPI",
                    published_at=pub_dt,
                    summary=article.get("description") or "",
                )
            )
        except Exception:
            continue
    return items


def get_news_feed(ticker: str) -> EnrichedNewsFeed:
    """
    Fetch, merge, deduplicate, score, and return a complete EnrichedNewsFeed.
    Includes rolling sentiment windows (1h/24h/7d), momentum signal, and
    source-credibility-weighted compound score.
    Never raises — returns an empty feed on total failure.
    """
    t = ticker.upper()

    yf_items: list[NewsItem] = yfinance_client.fetch_news(t)
    rss_items: list[NewsItem] = rss.fetch_news(t)
    newsapi_items: list[NewsItem] = _fetch_newsapi(t)

    # Deduplicate by normalised title prefix (first 60 chars)
    seen: set[str] = set()
    merged: list[NewsItem] = []
    for item in yf_items + rss_items + newsapi_items:
        key = item.title.lower().strip()[:60]
        if key and key not in seen:
            seen.add(key)
            merged.append(item)

    merged.sort(key=lambda i: i.published_at, reverse=True)
    merged = merged[:25]

    scored_items = analyze_news(merged, ticker=t)
    return build_enriched_feed(t, scored_items)
