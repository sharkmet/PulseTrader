"""
Unified data dispatcher.

Routing logic:
  OHLCV   — crypto: Binance primary → yfinance fallback
            stocks: yfinance only
  Quote   — yfinance primary (has both stocks + crypto); Binance supplemental for crypto
  News    — yfinance + NewsAPI (in news_fetcher)
  Search  — CoinGecko (crypto) + yfinance (stocks/ETFs), deduplicated
  Macro   — FRED (separate endpoint)
"""
from __future__ import annotations

import logging

from backend.app.models.schemas import NewsItem, OHLCVBar, Quote
from backend.app.services import coingecko_client, yfinance_client
from backend.app.services.sources import binance

logger = logging.getLogger(__name__)


def is_crypto(ticker: str) -> bool:
    """Heuristic: tickers ending in -USD/-USDT are crypto."""
    t = ticker.upper()
    return t.endswith("-USD") or t.endswith("-USDT")


def get_ohlcv(ticker: str, period: str = "3mo", interval: str = "1d") -> list[OHLCVBar]:
    """
    Fetch OHLCV bars.
    Crypto → Binance primary, yfinance fallback.
    Stocks → yfinance only.
    """
    if is_crypto(ticker):
        bars = binance.fetch_ohlcv(ticker, period=period, interval=interval)
        if bars:
            logger.debug("OHLCV for %s: Binance (%d bars)", ticker, len(bars))
            return bars
        logger.debug("Binance OHLCV empty for %s, falling back to yfinance", ticker)

    bars = yfinance_client.fetch_ohlcv(ticker, period=period, interval=interval)
    if bars:
        logger.debug("OHLCV for %s: yfinance (%d bars)", ticker, len(bars))
    return bars


def get_quote(ticker: str) -> Quote | None:
    """
    Fetch real-time quote.
    yfinance handles both stocks and crypto well; use it as primary.
    Fall back to Binance for crypto if yfinance fails.
    """
    quote = yfinance_client.fetch_quote(ticker)
    if quote is not None:
        return quote

    if is_crypto(ticker):
        logger.debug("yfinance quote empty for %s, trying Binance", ticker)
        return binance.fetch_quote(ticker)

    return None


def get_news(ticker: str) -> list[NewsItem]:
    return yfinance_client.fetch_news(ticker)


def search(query: str) -> list[dict]:
    """
    Unified search: CoinGecko for crypto + yfinance for stocks/ETFs.
    Deduplicates by ticker symbol.
    """
    crypto = coingecko_client.search_coins(query)
    stocks = yfinance_client.search_tickers(query)

    seen: set[str] = set()
    results: list[dict] = []
    for item in crypto + stocks:
        t = item.get("ticker", "").upper()
        if t and t not in seen:
            seen.add(t)
            results.append(item)
    return results[:10]
