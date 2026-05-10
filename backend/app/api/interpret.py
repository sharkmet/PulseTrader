"""News interpreter endpoint — Claude-powered theme extraction from headlines."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException

from backend.app.config import get_settings
from backend.app.database import SessionLocal
from backend.app.models.db import AiAnalysisCache
from backend.app.models.schemas import NewsInterpretation
from backend.app.services.ai_client import interpret_news
from backend.app.services.budget import BudgetExceeded, budget_status
from backend.app.services.news_fetcher import get_news_feed

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/assets", tags=["ai"])

_CACHE_KEY_PREFIX = "news_interpret:"


@router.post("/{ticker}/news/interpret", response_model=NewsInterpretation)
def interpret_ticker_news(ticker: str) -> NewsInterpretation:
    """
    Send recent headlines for `ticker` to Claude and receive back:
    - Narrative summary of what the news collectively signals
    - Key themes with per-theme sentiment
    - Bullish catalysts and risk factors

    Results are cached in SQLite for the same TTL as AI analyses.
    Returns 429 if the daily token budget has been reached.
    """
    settings = get_settings()
    t = ticker.upper()
    cache_key = f"{_CACHE_KEY_PREFIX}{t}"
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.cache_ttl_ai_analysis)

    # Check SQLite cache first
    with SessionLocal() as db:
        row = (
            db.query(AiAnalysisCache)
            .filter(
                AiAnalysisCache.ticker == cache_key,
                AiAnalysisCache.generated_at >= cutoff,
            )
            .order_by(AiAnalysisCache.generated_at.desc())
            .first()
        )
        if row:
            try:
                data = json.loads(row.analysis_json)
                data["cached"] = True
                return NewsInterpretation(**data)
            except Exception as exc:
                logger.warning("Cache parse failed for %s: %s", cache_key, exc)

    # Fetch news and run interpretation
    news_feed = get_news_feed(t)
    if not news_feed.items:
        raise HTTPException(status_code=404, detail=f"No recent news found for '{t}'")

    try:
        interpretation = interpret_news(t, news_feed.items)
    except BudgetExceeded as exc:
        bud = budget_status()
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Daily AI token budget exceeded",
                "spent_usd": bud["today_spent_usd"],
                "budget_usd": bud["daily_budget_usd"],
                "resets": "midnight UTC",
                "tip": "Increase DAILY_TOKEN_BUDGET_USD in .env to raise the cap",
            },
        ) from exc

    # Cache the result
    with SessionLocal() as db:
        try:
            db.add(AiAnalysisCache(
                ticker=cache_key,
                analysis_json=interpretation.model_dump_json(),
                generated_at=datetime.now(UTC),
            ))
            db.commit()
        except Exception as exc:
            logger.warning("Cache write failed for %s: %s", cache_key, exc)

    return interpretation
