"""
Binance public REST API — crypto OHLCV and 24h ticker.
No API key required for public endpoints.
Rate limit: 1200 requests/minute on the weight-based system.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from backend.app.models.schemas import OHLCVBar, Quote
from backend.app.services import circuit_breaker, rate_limiter
from backend.app.services.cache import cache
from backend.app.services.observability import observe

logger = logging.getLogger(__name__)
_SOURCE = "binance"
_BASE = "https://api.binance.com"
_TIMEOUT = 10.0

# yfinance interval → Binance kline interval
_INTERVAL_MAP: dict[str, str] = {
    "1m": "1m",
    "2m": "3m",    # binance has 3m, not 2m
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "60m": "1h",
    "90m": "1h",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "5d": "1d",    # no 5d, use 1d
    "1wk": "1w",
    "1mo": "1M",
}

# yfinance period → approximate Binance limit (number of klines)
_PERIOD_LIMIT: dict[str, int] = {
    "1d": 288,    # 1d at 5m
    "5d": 120,    # 5d at 1h
    "1mo": 31,
    "3mo": 90,
    "6mo": 180,
    "1y": 365,
    "2y": 500,    # capped at Binance's 1000 max
    "5y": 500,
}


def _to_binance_symbol(ticker: str) -> str | None:
    """Convert BTC-USD → BTCUSDT. Returns None if not a recognised crypto format."""
    t = ticker.upper()
    if t.endswith("-USD"):
        return t.replace("-USD", "USDT")
    if t.endswith("-USDT"):
        return t.replace("-", "")
    return None


def _get(path: str, params: dict | None = None) -> list | dict | None:
    cb = circuit_breaker.get(_SOURCE)
    rate_limiter.acquire(_SOURCE)
    try:
        with cb, observe(_SOURCE, path.split("/")[-1]):
            with httpx.Client(timeout=_TIMEOUT) as client:
                r = client.get(f"{_BASE}{path}", params=params or {})
                r.raise_for_status()
                return r.json()
    except Exception as exc:
        logger.warning("Binance request failed %s: %s", path, exc)
        return None


def fetch_ohlcv(ticker: str, period: str = "3mo", interval: str = "1d") -> list[OHLCVBar]:
    """Fetch OHLCV klines. Returns [] if ticker is not a crypto or request fails."""
    symbol = _to_binance_symbol(ticker)
    if not symbol:
        return []

    cache_key = f"binance_ohlcv:{symbol}:{period}:{interval}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    bi = _INTERVAL_MAP.get(interval, "1d")
    limit = min(_PERIOD_LIMIT.get(period, 90), 1000)

    data = _get("/api/v3/klines", {"symbol": symbol, "interval": bi, "limit": limit})
    if not data or not isinstance(data, list):
        return []

    bars: list[OHLCVBar] = []
    for k in data:
        try:
            bars.append(
                OHLCVBar(
                    timestamp=datetime.fromtimestamp(k[0] / 1000, tz=UTC),
                    open=float(k[1]),
                    high=float(k[2]),
                    low=float(k[3]),
                    close=float(k[4]),
                    volume=float(k[5]),
                )
            )
        except (IndexError, ValueError):
            continue

    # Cache 5 minutes for daily bars, shorter for intraday
    ttl = 300 if interval in ("1d", "1wk") else 60
    cache.set(cache_key, bars, ttl)
    return bars


def fetch_quote(ticker: str) -> Quote | None:
    """Fetch 24h rolling ticker stats. Returns None for non-crypto tickers."""
    symbol = _to_binance_symbol(ticker)
    if not symbol:
        return None

    cache_key = f"binance_quote:{symbol}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    data = _get("/api/v3/ticker/24hr", {"symbol": symbol})
    if not data or not isinstance(data, dict):
        return None

    try:
        price = float(data["lastPrice"])
        prev_close = float(data["prevClosePrice"])
        change_abs = price - prev_close
        change_pct = float(data["priceChangePercent"])
        volume = float(data["volume"])

        quote = Quote(
            ticker=ticker.upper(),
            name=ticker.upper(),  # Binance doesn't return a display name
            price=price,
            change_1d=round(change_abs, 6),
            change_1d_pct=round(change_pct, 4),
            volume=volume,
            market_cap=None,
            asset_type="crypto",
            last_updated=datetime.now(UTC),
        )
    except (KeyError, ValueError) as exc:
        logger.warning("Binance quote parse failed for %s: %s", symbol, exc)
        return None

    cache.set(cache_key, quote, 30)  # 30s TTL for quotes
    return quote


def supports(ticker: str) -> bool:
    """True if this source can handle the ticker (i.e. it's crypto-formatted)."""
    return _to_binance_symbol(ticker) is not None
