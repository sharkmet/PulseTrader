"""
Full-snapshot endpoint — aggregates price, indicators, pulse score, news,
and multi-timeframe analysis in a single API call.

Use this instead of making 4-6 separate calls from a frontend or script.
Fields that fail (network error, insufficient data) are returned as None with
an entry in `errors[]` rather than aborting the whole request.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query

from backend.app.models.schemas import AssetSnapshot
from backend.app.services import data_fetcher
from backend.app.services.indicators import compute_indicators, compute_multi_timeframe
from backend.app.services.news_fetcher import get_news_feed
from backend.app.services.pulse_score import compute_pulse_score

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/assets", tags=["snapshot"])


@router.get(
    "/{ticker}",
    response_model=AssetSnapshot,
    summary="Full asset snapshot",
    response_description=(
        "Complete analysis bundle: quote, indicators, pulse score, "
        "optional news and multi-timeframe data."
    ),
)
def get_asset_snapshot(
    ticker: str,
    period: str = Query("6mo", description="OHLCV lookback period (1d 5d 1mo 3mo 6mo 1y 2y 5y)"),
    interval: str = Query("1d", description="Bar interval (5m 15m 1h 1d 1wk 1mo)"),
    include_news: bool = Query(True, description="Include news feed with sentiment analysis"),
    include_mtf: bool = Query(False, description="Include multi-timeframe analysis (3 extra fetches)"),
    include_macro: bool = Query(False, description="Include FRED macro context (requires FRED_API_KEY)"),
) -> AssetSnapshot:
    """
    Aggregate endpoint — equivalent to calling quote + indicators + pulse-score
    + (optionally) news, multi-timeframe, and macro in a single request.

    Non-fatal failures (bad network, missing FRED key, insufficient bars)
    are captured in `errors[]` and the snapshot is returned with those
    fields set to `null` rather than returning an error response.
    """
    t = ticker.upper()
    now = datetime.now(UTC)
    errors: list[str] = []
    sources_used: list[str] = []

    # ── OHLCV bars (required for everything downstream) ───────────────────────
    bars = data_fetcher.get_ohlcv(t, period=period, interval=interval)
    if not bars:
        raise HTTPException(status_code=404, detail=f"No price data found for '{t}'")
    sources_used.append("yfinance_ohlcv")

    # ── Real-time quote ───────────────────────────────────────────────────────
    quote = None
    try:
        quote = data_fetcher.get_quote(t)
        if quote:
            sources_used.append("yfinance_quote")
    except Exception as exc:
        errors.append(f"Quote unavailable: {exc}")

    # ── Indicator snapshot ────────────────────────────────────────────────────
    snap = None
    try:
        snap = compute_indicators(t, bars)
        if snap:
            sources_used.append("ta_indicators")
    except Exception as exc:
        errors.append(f"Indicators failed: {exc}")

    # ── News feed (optional) ──────────────────────────────────────────────────
    news_feed = None
    if include_news:
        try:
            news_feed = get_news_feed(t)
            if news_feed.items:
                sources_used.append("news")
        except Exception as exc:
            errors.append(f"News unavailable: {exc}")

    # ── Multi-timeframe (optional, 3 extra fetches) ───────────────────────────
    mtf = None
    if include_mtf:
        try:
            tf_specs = {"1h": ("5d", "1h"), "1d": ("6mo", "1d"), "1w": ("5y", "1wk")}
            bars_by_tf = {}
            for label, (p, iv) in tf_specs.items():
                b = data_fetcher.get_ohlcv(t, period=p, interval=iv)
                if b:
                    bars_by_tf[label] = b
            if bars_by_tf:
                mtf = compute_multi_timeframe(t, bars_by_tf)
                sources_used.append("multi_timeframe")
        except Exception as exc:
            errors.append(f"MTF computation failed: {exc}")

    # ── Macro context (optional) ──────────────────────────────────────────────
    macro_context = None
    if include_macro:
        try:
            from backend.app.services.sources.fred import macro_context_string
            ctx = macro_context_string()
            if "unavailable" not in ctx.lower():
                macro_context = ctx
                sources_used.append("fred_macro")
        except Exception as exc:
            errors.append(f"Macro data unavailable: {exc}")

    # ── Pulse score ───────────────────────────────────────────────────────────
    pulse = None
    try:
        pulse = compute_pulse_score(
            ticker=t,
            bars=bars,
            snap=snap,
            news_feed=news_feed,
            ai_score=50.0,  # AI component skipped in snapshot to avoid token cost
            mtf_snapshot=mtf,
        )
    except Exception as exc:
        errors.append(f"Pulse score failed: {exc}")

    asset_type = "crypto" if ("-USD" in t or "-USDT" in t) else "stock"

    return AssetSnapshot(
        ticker=t,
        asset_type=asset_type,
        fetched_at=now,
        quote=quote,
        indicators=snap,
        pulse_score=pulse,
        news=news_feed if include_news else None,
        multi_timeframe=mtf,
        macro_context=macro_context,
        data_sources_used=sources_used,
        errors=errors,
    )
