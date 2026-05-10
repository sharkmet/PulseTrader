"""CoinGecko free-tier REST client — supplementary crypto data and search."""
from __future__ import annotations

import logging

import httpx

from backend.app.config import get_settings
from backend.app.services import circuit_breaker, rate_limiter
from backend.app.services.cache import cache
from backend.app.services.observability import observe

logger = logging.getLogger(__name__)
_SOURCE = "coingecko"
_BASE = "https://api.coingecko.com/api/v3"
_TIMEOUT = 10.0

SYMBOL_TO_ID: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "MATIC": "matic-network",
    "LINK": "chainlink",
    "LTC": "litecoin",
    "UNI": "uniswap",
    "ATOM": "cosmos",
    "SHIB": "shiba-inu",
    "TRX": "tron",
    "TON": "the-open-network",
    "SUI": "sui",
}


def _headers() -> dict[str, str]:
    settings = get_settings()
    h: dict[str, str] = {"Accept": "application/json"}
    if settings.coingecko_demo_key:
        h["x-cg-demo-api-key"] = settings.coingecko_demo_key
    return h


def _get(path: str, params: dict | None = None) -> dict | list | None:
    rate_limiter.acquire(_SOURCE)
    cb = circuit_breaker.get(_SOURCE)
    try:
        with cb, observe(_SOURCE, "get", ticker=path):
            with httpx.Client(timeout=_TIMEOUT) as client:
                r = client.get(f"{_BASE}{path}", params=params or {}, headers=_headers())
                r.raise_for_status()
                return r.json()
    except Exception as exc:
        logger.warning("CoinGecko request failed %s: %s", path, exc)
        return None


def ticker_to_coin_id(ticker: str) -> str | None:
    """Convert yfinance-style ticker (BTC-USD) or bare symbol to CoinGecko ID."""
    symbol = ticker.upper().replace("-USD", "").replace("-USDT", "")
    if symbol in SYMBOL_TO_ID:
        return SYMBOL_TO_ID[symbol]
    return search_coin_id(symbol)


def search_coin_id(query: str) -> str | None:
    data = _get("/search", {"query": query})
    if not data or not isinstance(data, dict):
        return None
    coins = data.get("coins", [])
    if coins:
        return coins[0].get("id")
    return None


def fetch_coin_market_data(coin_id: str) -> dict | None:
    """Fetch rich market data (volume, ATH, dominance, etc.)."""
    key = f"cg_market:{coin_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    data = _get(
        f"/coins/{coin_id}",
        {
            "localization": "false",
            "tickers": "false",
            "community_data": "false",
            "developer_data": "false",
        },
    )
    if data and isinstance(data, dict):
        settings = get_settings()
        cache.set(key, data, settings.cache_ttl_quote)
    return data


def search_coins(query: str) -> list[dict]:
    """Return [{ticker, name, coin_id, type}] for autocomplete."""
    data = _get("/search", {"query": query})
    if not data or not isinstance(data, dict):
        return []
    coins = data.get("coins", [])[:8]
    return [
        {
            "ticker": f"{c.get('symbol', '').upper()}-USD",
            "name": c.get("name", ""),
            "coin_id": c.get("id", ""),
            "type": "crypto",
        }
        for c in coins
        if c.get("symbol")
    ]
