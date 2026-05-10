"""AI deep-dive analysis endpoint."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException

from backend.app.config import get_settings
from backend.app.database import SessionLocal
from backend.app.models.db import AiAnalysisCache
from backend.app.models.schemas import AiAnalysis
from backend.app.services import data_fetcher
from backend.app.services.ai_client import generate_analysis
from backend.app.services.budget import BudgetExceeded, budget_status
from backend.app.services.indicators import compute_indicators
from backend.app.services.news_fetcher import get_news_feed

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/assets", tags=["ai"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/{ticker}/deep-dive", response_model=AiAnalysis)
def deep_dive(ticker: str) -> AiAnalysis:
    """
    Generate an AI deep-dive analysis for the given ticker via Claude.
    Results are cached in SQLite for 15 minutes (configurable via CACHE_TTL_AI_ANALYSIS).
    Returns HTTP 429 if the daily token budget is exhausted.
    """
    settings = get_settings()
    t = ticker.upper()
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.cache_ttl_ai_analysis)

    # Check SQLite cache (TTL from config, default 15 min)
    with SessionLocal() as db:
        row = (
            db.query(AiAnalysisCache)
            .filter(
                AiAnalysisCache.ticker == t,
                AiAnalysisCache.generated_at >= cutoff,
            )
            .order_by(AiAnalysisCache.generated_at.desc())
            .first()
        )
        if row:
            try:
                data = json.loads(row.analysis_json)
                data["cached"] = True
                return AiAnalysis(**data)
            except Exception as exc:
                logger.warning("Cache parse failed for %s: %s", t, exc)

    # Fetch fresh data
    bars = data_fetcher.get_ohlcv(t, period="3mo", interval="1d")
    if not bars:
        raise HTTPException(status_code=404, detail=f"No price data for '{t}'")

    snap = compute_indicators(t, bars)
    news_feed = get_news_feed(t)

    try:
        analysis = generate_analysis(ticker=t, bars=bars, snap=snap, news_feed=news_feed)
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

    # Only cache successful analyses — never cache error stubs
    _is_real = (
        analysis.model_used not in ("none", "")
        and not analysis.summary.startswith("Analysis temporarily unavailable")
        and not analysis.summary.startswith("Add GOOGLE_API_KEY")
        and not analysis.summary.startswith("Add ANTHROPIC_API_KEY")
        and len(analysis.bullish_factors) > 0
    )
    if _is_real:
        with SessionLocal() as db:
            try:
                db.add(AiAnalysisCache(
                    ticker=t,
                    analysis_json=analysis.model_dump_json(),
                    generated_at=datetime.now(UTC),
                ))
                db.commit()
            except Exception as exc:
                logger.warning("Cache write failed for %s: %s", t, exc)

    return analysis


@admin_router.delete("/ai-cache", tags=["admin"])
def clear_ai_cache() -> dict:
    """
    Clear ALL cached AI analyses. Call this after updating your API key
    so stale error responses don't block fresh generation.
    """
    with SessionLocal() as db:
        deleted = db.query(AiAnalysisCache).delete()
        db.commit()
    logger.info("Cleared %d AI analysis cache entries", deleted)
    return {"cleared": deleted, "message": "AI cache cleared — next deep-dive request will call the AI provider fresh"}
