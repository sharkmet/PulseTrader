"""Pulse Score endpoint — v2 formula with 7-component decomposition."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.app.config import get_settings
from backend.app.models.schemas import PulseScoreBreakdown
from backend.app.services import data_fetcher
from backend.app.services.ai_client import get_ai_score
from backend.app.services.cache import cache
from backend.app.services.indicators import compute_indicators, compute_multi_timeframe
from backend.app.services.news_fetcher import get_news_feed
from backend.app.services.pulse_score import compute_pulse_score
from backend.app.services.score_weights import get_scoring_config

router = APIRouter(prefix="/assets", tags=["score"])


@router.get("/{ticker}/pulse-score", response_model=PulseScoreBreakdown)
def get_pulse_score(
    ticker: str,
    include_ai: bool = Query(True, description="Include Claude AI component (costs tokens)"),
    include_mtf: bool = Query(True, description="Include multi-timeframe agreement (3 bar fetches)"),
    period: str = Query("6mo"),
    interval: str = Query("1d"),
) -> PulseScoreBreakdown:
    """
    Compute the full 7-component Pulse Score.

    Components: trend (25%), momentum (20%), volatility (10%), volume (10%),
    sentiment (15%), multi-timeframe agreement (10%), AI overlay (10%).

    All weights are configurable via config/score_weights.toml.
    Formula version is included in the response for backtest comparison.
    """
    settings = get_settings()
    t = ticker.upper()
    cache_key = f"pulse_score_v2:{t}:{period}:{interval}:{include_ai}:{include_mtf}"

    cached = cache.get(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    bars = data_fetcher.get_ohlcv(t, period=period, interval=interval)
    if not bars:
        raise HTTPException(status_code=404, detail=f"No price data for '{t}'")

    snap = compute_indicators(t, bars)
    news_feed = get_news_feed(t)

    # Multi-timeframe (optional — 3 extra bar fetches)
    mtf_snapshot = None
    if include_mtf:
        tf_specs = {"1h": ("5d", "1h"), "1d": ("6mo", "1d"), "1w": ("5y", "1wk")}
        bars_by_tf = {}
        for label, (p, iv) in tf_specs.items():
            b = data_fetcher.get_ohlcv(t, period=p, interval=iv)
            if b:
                bars_by_tf[label] = b
        if bars_by_tf:
            mtf_snapshot = compute_multi_timeframe(t, bars_by_tf)

    # AI component (optional — costs tokens)
    ai_score = 50.0
    if include_ai and settings.has_anthropic_key:
        rsi_str = f"{snap.rsi_14.value:.1f}" if (snap and snap.rsi_14.value is not None) else "N/A"
        sent_str = f"{news_feed.rolling_sentiment:+.2f}"
        context = f"{t}: RSI={rsi_str}, sentiment={sent_str}"
        ai_score = get_ai_score(t, context)

    score = compute_pulse_score(
        ticker=t,
        bars=bars,
        snap=snap,
        news_feed=news_feed,
        ai_score=ai_score,
        mtf_snapshot=mtf_snapshot,
    )
    cache.set(cache_key, score, settings.cache_ttl_score)
    return score


@router.get("/{ticker}/pulse-score/weights", tags=["score"])
def get_score_weights() -> dict:
    """Return the active scoring weights and formula version."""
    cfg = get_scoring_config()
    return {
        "version": cfg.version,
        "components": cfg.components.as_dict(),
        "sub_weights": cfg.sub_weights,
    }
