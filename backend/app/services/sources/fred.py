"""
FRED (Federal Reserve Economic Data) adapter.
Free API — requires FRED_API_KEY from https://fred.stlouisfed.org/docs/api/api_key.html
Rate limit: 120 requests/minute.

Series fetched:
  VIXCLS    — CBOE Volatility Index (daily)
  FEDFUNDS  — Effective Federal Funds Rate (monthly)
  DGS10     — 10-Year Treasury Constant Maturity Rate (daily)
  DGS2      — 2-Year Treasury Constant Maturity Rate (daily)
  CPIAUCSL  — CPI All Urban Consumers (monthly, YoY inflation proxy)
  UNRATE    — Unemployment Rate (monthly)
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from backend.app.config import get_settings
from backend.app.services import circuit_breaker, rate_limiter
from backend.app.services.cache import cache
from backend.app.services.observability import observe

logger = logging.getLogger(__name__)
_SOURCE = "fred"
_BASE = "https://api.stlouisfed.org/fred"
_TIMEOUT = 10.0

# FRED series IDs we care about
SERIES: dict[str, str] = {
    "vix": "VIXCLS",
    "fed_funds_rate": "FEDFUNDS",
    "treasury_10y": "DGS10",
    "treasury_2y": "DGS2",
    "cpi": "CPIAUCSL",
    "unemployment": "UNRATE",
}


def _get(path: str, params: dict) -> dict | None:
    settings = get_settings()
    if not settings.has_fred_key:
        return None

    rate_limiter.acquire(_SOURCE)
    cb = circuit_breaker.get(_SOURCE)
    params["api_key"] = settings.fred_api_key
    params["file_type"] = "json"

    try:
        with cb, observe(_SOURCE, path.split("/")[-1]):
            with httpx.Client(timeout=_TIMEOUT) as client:
                r = client.get(f"{_BASE}{path}", params=params)
                r.raise_for_status()
                return r.json()
    except Exception as exc:
        logger.warning("FRED request failed %s: %s", path, exc)
        return None


def _latest_value(series_id: str) -> float | None:
    """Return the most recent non-null observation for a series."""
    cache_key = f"fred:{series_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    data = _get(
        "/series/observations",
        {
            "series_id": series_id,
            "sort_order": "desc",
            "limit": 5,  # grab a few in case the latest is "."
            "observation_start": "2020-01-01",
        },
    )
    if not data:
        return None

    for obs in data.get("observations", []):
        val_str = obs.get("value", ".")
        if val_str != ".":
            try:
                value = float(val_str)
                # Cache: daily series for 6h, monthly for 24h
                cache.set(cache_key, value, 21_600)
                return value
            except ValueError:
                continue
    return None


def _yfinance_macro_fallback() -> dict[str, float | None]:
    """
    Fetch key macro proxies from yfinance when no FRED key is set.
    Uses: ^VIX, ^TNX (10Y yield), ^IRX (3M T-bill), GLD, TLT.
    """
    cache_key = "yf_macro_fallback"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    result: dict[str, float | None] = {
        "vix": None, "treasury_10y": None, "treasury_2y": None,
        "yield_spread_10y2y": None, "cpi": None, "unemployment": None,
        "fed_funds_rate": None, "fetched_at": None,
    }
    try:
        import yfinance as yf
        tickers = yf.download(
            ["^VIX", "^TNX", "^FVX", "SPY"],
            period="2d", interval="1d", progress=False, auto_adjust=True,
        )
        if not tickers.empty:
            def _last(sym: str) -> float | None:
                try:
                    col = ("Close", sym) if isinstance(tickers.columns, object) and hasattr(tickers.columns, "levels") else "Close"
                    val = tickers[col][sym].dropna().iloc[-1] if isinstance(tickers.columns, object) and hasattr(tickers.columns, "levels") else tickers["Close"].dropna().iloc[-1]
                    return round(float(val), 3)
                except Exception:
                    return None

            # Simpler approach: fetch individually
            for sym, field in [("^VIX", "vix"), ("^TNX", "treasury_10y"), ("^FVX", "treasury_2y")]:
                try:
                    v = yf.Ticker(sym).fast_info.last_price
                    result[field] = round(float(v), 3) if v else None
                except Exception:
                    pass

            t10 = result.get("treasury_10y")
            t2 = result.get("treasury_2y")
            result["yield_spread_10y2y"] = round(t10 - t2, 3) if t10 and t2 else None
    except Exception as exc:
        logger.debug("yfinance macro fallback failed: %s", exc)

    result["fetched_at"] = datetime.now(UTC).isoformat()  # type: ignore[assignment]
    cache.set(cache_key, result, 1800)  # 30-min cache
    return result


def fetch_snapshot() -> dict[str, float | None]:
    """
    Return a snapshot of key macro indicators.
    Uses FRED if key is set, otherwise falls back to yfinance proxies.
    """
    settings = get_settings()
    if not settings.has_fred_key:
        logger.debug("FRED_API_KEY not set — using yfinance macro fallback")
        return _yfinance_macro_fallback()

    cache_key = "fred:snapshot"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    snapshot: dict[str, float | None] = {}
    for field, series_id in SERIES.items():
        snapshot[field] = _latest_value(series_id)

    # Derived: yield curve spread (10Y - 2Y)
    t10 = snapshot.get("treasury_10y")
    t2 = snapshot.get("treasury_2y")
    snapshot["yield_spread_10y2y"] = round(t10 - t2, 3) if t10 is not None and t2 is not None else None

    # Macro regime summary
    snapshot["fetched_at"] = datetime.now(UTC).isoformat()  # type: ignore[assignment]
    cache.set(cache_key, snapshot, 3_600)  # 1h cache for the full snapshot
    return snapshot


def macro_context_string() -> str:
    """Return a single-line macro context summary for use in AI prompts."""
    snap = fetch_snapshot()
    if not snap:
        return "Macro data unavailable (no FRED_API_KEY configured)"

    parts: list[str] = []
    if snap.get("vix") is not None:
        vix = snap["vix"]
        label = "elevated" if vix > 25 else "calm"  # type: ignore[operator]
        parts.append(f"VIX={vix:.1f} ({label})")
    if snap.get("fed_funds_rate") is not None:
        parts.append(f"Fed Funds={snap['fed_funds_rate']:.2f}%")
    if snap.get("treasury_10y") is not None:
        parts.append(f"10Y={snap['treasury_10y']:.2f}%")
    if snap.get("yield_spread_10y2y") is not None:
        spread = snap["yield_spread_10y2y"]
        inv = " (INVERTED)" if spread < 0 else ""  # type: ignore[operator]
        parts.append(f"Spread={spread:.2f}%{inv}")
    if snap.get("cpi") is not None:
        parts.append(f"CPI={snap['cpi']:.1f}")

    return " | ".join(parts) if parts else "Macro data unavailable"
