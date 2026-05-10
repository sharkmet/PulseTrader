"""Asset data endpoints — OHLCV, quotes, indicators, search, and macro context."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.app.models.schemas import IndicatorSnapshot, MultiTimeframeSnapshot, OHLCVBar, Quote
from backend.app.services import data_fetcher
from backend.app.services.indicators import compute_indicators, compute_multi_timeframe

router = APIRouter(prefix="/assets", tags=["assets"])
macro_router = APIRouter(prefix="/macro", tags=["macro"])


@router.get("/search")
def search_assets(q: str = Query(..., min_length=1)) -> list[dict]:
    return data_fetcher.search(q)


@router.get("/{ticker}/quote", response_model=Quote)
def get_quote(ticker: str) -> Quote:
    quote = data_fetcher.get_quote(ticker)
    if quote is None:
        raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' not found")
    return quote


@router.get("/{ticker}/ohlcv", response_model=list[OHLCVBar])
def get_ohlcv(
    ticker: str,
    period: str = Query("3mo", description="1d 5d 1mo 3mo 6mo 1y 2y 5y"),
    interval: str = Query("1d", description="5m 15m 30m 1h 1d 1wk 1mo"),
) -> list[OHLCVBar]:
    bars = data_fetcher.get_ohlcv(ticker, period=period, interval=interval)
    if not bars:
        raise HTTPException(status_code=404, detail=f"No OHLCV data for '{ticker}'")
    return bars


@router.get("/{ticker}/indicators", response_model=IndicatorSnapshot)
def get_indicators(
    ticker: str,
    period: str = Query("6mo"),
    interval: str = Query("1d"),
) -> IndicatorSnapshot:
    bars = data_fetcher.get_ohlcv(ticker, period=period, interval=interval)
    if not bars:
        raise HTTPException(status_code=404, detail=f"No data for '{ticker}'")
    snap = compute_indicators(ticker, bars)
    if snap is None:
        raise HTTPException(
            status_code=422,
            detail=f"Insufficient data to compute indicators for '{ticker}' (need ≥30 bars)",
        )
    return snap


@router.get("/{ticker}/indicators/multi-timeframe", response_model=MultiTimeframeSnapshot)
def get_multi_timeframe(ticker: str) -> MultiTimeframeSnapshot:
    """
    Compute indicators across 1h, 1D, and 1W timeframes and return a cross-timeframe
    agreement score with direction consensus.
    Timeframes with insufficient data are skipped silently.
    """
    t = ticker.upper()
    timeframe_specs = {
        "1h": ("5d", "1h"),
        "1d": ("6mo", "1d"),
        "1w": ("5y", "1wk"),
    }
    bars_by_tf: dict[str, list[OHLCVBar]] = {}
    for label, (period, interval) in timeframe_specs.items():
        bars = data_fetcher.get_ohlcv(t, period=period, interval=interval)
        if bars:
            bars_by_tf[label] = bars

    if not bars_by_tf:
        raise HTTPException(status_code=404, detail=f"No price data for '{t}'")

    return compute_multi_timeframe(t, bars_by_tf)


# ── Macro endpoints ────────────────────────────────────────────────────────────

@macro_router.get("/snapshot", tags=["macro"])
def get_macro_snapshot() -> dict:
    """
    Current macro indicators from FRED: VIX, Fed Funds Rate, 10Y/2Y Treasury yields,
    yield spread, CPI, unemployment. Requires FRED_API_KEY in .env.
    Returns empty dict with a message if no key is configured.
    """
    from backend.app.services.sources.fred import fetch_snapshot
    snapshot = fetch_snapshot()
    # fetch_snapshot now always returns something (FRED or yfinance fallback)
    if not snapshot or not any(v is not None for k, v in snapshot.items() if k not in ("fetched_at",)):
        return {
            "available": False,
            "message": "Macro data unavailable — check logs",
        }
    from backend.app.config import get_settings
    source = "FRED" if get_settings().has_fred_key else "yfinance (FRED_API_KEY not set)"
    return {"available": True, "source": source, **snapshot}


@macro_router.get("/context", tags=["macro"])
def get_macro_context() -> dict:
    """One-line macro context string suitable for use in prompts or dashboards."""
    from backend.app.services.sources.fred import macro_context_string
    return {"context": macro_context_string()}
