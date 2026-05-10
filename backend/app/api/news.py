"""News and sentiment endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.app.models.schemas import EnrichedNewsFeed
from backend.app.services.news_fetcher import get_news_feed

router = APIRouter(prefix="/assets", tags=["news"])


@router.get("/{ticker}/news", response_model=EnrichedNewsFeed)
def get_news(ticker: str) -> EnrichedNewsFeed:
    """
    Fetch, score, and return enriched news feed including:
    - Sentiment per article (VADER/FinBERT/Claude)
    - Source credibility weights
    - Rolling sentiment windows (1h / 24h / 7d)
    - Sentiment momentum (rising / falling / stable)
    - Top mentioned tickers/companies
    """
    feed = get_news_feed(ticker.upper())
    if not feed.items:
        raise HTTPException(status_code=404, detail=f"No news found for '{ticker}'")
    return feed
