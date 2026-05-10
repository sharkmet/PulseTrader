"""yfinance wrapper — stocks, ETFs, and crypto (BTC-USD style tickers)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

import yfinance as yf

from backend.app.config import get_settings
from backend.app.models.schemas import NewsItem, OHLCVBar, Quote
from backend.app.services import circuit_breaker, rate_limiter
from backend.app.services.cache import cache
from backend.app.services.observability import observe

logger = logging.getLogger(__name__)
_SOURCE = "yfinance"


def _normalize_ticker(ticker: str) -> str:
    return ticker.upper().strip()


def fetch_ohlcv(ticker: str, period: str = "3mo", interval: str = "1d") -> list[OHLCVBar]:
    """
    Fetch OHLCV history.
    period:   1d 5d 1mo 3mo 6mo 1y 2y 5y 10y ytd max
    interval: 1m 2m 5m 15m 30m 60m 90m 1h 1d 5d 1wk 1mo 3mo
    """
    settings = get_settings()
    t = _normalize_ticker(ticker)
    key = f"ohlcv:{t}:{period}:{interval}"
    cached = cache.get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    cb = circuit_breaker.get(_SOURCE)
    rate_limiter.acquire(_SOURCE)

    try:
        with cb, observe(_SOURCE, "ohlcv", ticker=t):
            yticker = yf.Ticker(t)
            hist = yticker.history(period=period, interval=interval, auto_adjust=True)
    except Exception as exc:
        logger.warning("yfinance OHLCV fetch failed for %s: %s", t, exc)
        return []

    if hist.empty:
        return []

    bars: list[OHLCVBar] = []
    for ts, row in hist.iterrows():
        try:
            bars.append(
                OHLCVBar(
                    timestamp=ts.to_pydatetime().replace(tzinfo=UTC),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row["Volume"]),
                )
            )
        except (ValueError, KeyError):
            continue

    cache.set(key, bars, settings.cache_ttl_ohlcv)
    return bars


def fetch_quote(ticker: str) -> Quote | None:
    settings = get_settings()
    t = _normalize_ticker(ticker)
    key = f"quote:{t}"
    cached = cache.get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    cb = circuit_breaker.get(_SOURCE)
    rate_limiter.acquire(_SOURCE)

    try:
        with cb, observe(_SOURCE, "quote", ticker=t):
            yticker = yf.Ticker(t)
            info = yticker.fast_info
            full_info = yticker.info
    except Exception as exc:
        logger.warning("yfinance quote fetch failed for %s: %s", t, exc)
        return None

    try:
        price = float(info.last_price or full_info.get("currentPrice", 0))
        prev_close = float(info.previous_close or full_info.get("previousClose", price))
        change_abs = price - prev_close
        change_pct = (change_abs / prev_close * 100) if prev_close else 0.0
        volume = float(info.three_month_average_volume or full_info.get("volume", 0))
        market_cap = full_info.get("marketCap") or info.market_cap
        name = full_info.get("longName") or full_info.get("shortName") or t
        asset_type = "crypto" if "-USD" in t else "stock"

        quote = Quote(
            ticker=t,
            name=name,
            price=price,
            change_1d=round(change_abs, 4),
            change_1d_pct=round(change_pct, 4),
            volume=volume,
            market_cap=float(market_cap) if market_cap else None,
            asset_type=asset_type,
            last_updated=datetime.now(UTC),
        )
    except Exception as exc:
        logger.warning("yfinance quote parse failed for %s: %s", t, exc)
        return None

    cache.set(key, quote, settings.cache_ttl_quote)
    return quote


def fetch_news(ticker: str) -> list[NewsItem]:
    settings = get_settings()
    t = _normalize_ticker(ticker)
    key = f"news_yf:{t}"
    cached = cache.get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    cb = circuit_breaker.get(_SOURCE)
    rate_limiter.acquire(_SOURCE)

    try:
        with cb, observe(_SOURCE, "news", ticker=t):
            yticker = yf.Ticker(t)
            raw_news = yticker.news or []
    except Exception as exc:
        logger.warning("yfinance news fetch failed for %s: %s", t, exc)
        return []

    items: list[NewsItem] = []
    for article in raw_news[:20]:
        try:
            pub_time = datetime.fromtimestamp(article.get("providerPublishTime", 0), tz=UTC)
            items.append(
                NewsItem(
                    title=article.get("title", ""),
                    url=article.get("link", ""),
                    publisher=article.get("publisher", ""),
                    published_at=pub_time,
                    summary=article.get("summary", ""),
                )
            )
        except Exception:
            continue

    cache.set(key, items, settings.cache_ttl_news)
    return items


def search_tickers(query: str) -> list[dict]:
    """Search for tickers by name or symbol. Returns list of {ticker, name, type}."""
    rate_limiter.acquire(_SOURCE)
    try:
        with observe(_SOURCE, "search"):
            results = yf.Search(query, max_results=8)
            quotes = getattr(results, "quotes", []) or []
    except Exception as exc:
        logger.warning("yfinance search failed: %s", exc)
        return []

    out = []
    for q in quotes:
        symbol = q.get("symbol", "")
        name = q.get("longname") or q.get("shortname") or symbol
        qtype = q.get("quoteType", "EQUITY").lower()
        if symbol:
            out.append({"ticker": symbol, "name": name, "type": qtype})
    return out
