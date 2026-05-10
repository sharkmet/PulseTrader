"""Tests for yfinance_client — all external calls mocked."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


def _make_hist(prices: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(prices), freq="D")
    return pd.DataFrame(
        {"Open": prices, "High": [p * 1.01 for p in prices],
         "Low": [p * 0.99 for p in prices], "Close": prices,
         "Volume": [1_000_000] * len(prices)},
        index=idx,
    )


@pytest.fixture(autouse=True)
def _reset_cache():
    from backend.app.services.cache import cache
    cache.clear()
    yield
    cache.clear()


# ── fetch_ohlcv ───────────────────────────────────────────────────────────────

def test_fetch_ohlcv_returns_bars():
    from backend.app.services.yfinance_client import fetch_ohlcv
    hist = _make_hist([100.0, 101.0, 102.0])
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = hist
    with patch("backend.app.services.yfinance_client.yf.Ticker", return_value=mock_ticker):
        bars = fetch_ohlcv("AAPL", period="5d", interval="1d")
    assert len(bars) == 3
    assert bars[0].close == 100.0
    assert bars[2].close == 102.0


def test_fetch_ohlcv_returns_empty_on_network_error():
    from backend.app.services.yfinance_client import fetch_ohlcv
    with patch("backend.app.services.yfinance_client.yf.Ticker", side_effect=Exception("network")):
        bars = fetch_ohlcv("AAPL")
    assert bars == []


def test_fetch_ohlcv_returns_empty_for_empty_dataframe():
    from backend.app.services.yfinance_client import fetch_ohlcv
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = pd.DataFrame()
    with patch("backend.app.services.yfinance_client.yf.Ticker", return_value=mock_ticker):
        bars = fetch_ohlcv("UNKNOWN")
    assert bars == []


def test_fetch_ohlcv_caches_result():
    from backend.app.services.yfinance_client import fetch_ohlcv
    hist = _make_hist([100.0])
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = hist
    with patch("backend.app.services.yfinance_client.yf.Ticker", return_value=mock_ticker) as mk:
        fetch_ohlcv("AAPL", period="1d", interval="1d")
        fetch_ohlcv("AAPL", period="1d", interval="1d")
    # yf.Ticker only called once (second call uses cache)
    assert mk.call_count == 1


def test_fetch_ohlcv_bars_sorted_ascending():
    from backend.app.services.yfinance_client import fetch_ohlcv
    hist = _make_hist([105.0, 103.0, 102.0])
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = hist
    with patch("backend.app.services.yfinance_client.yf.Ticker", return_value=mock_ticker):
        bars = fetch_ohlcv("AAPL")
    timestamps = [b.timestamp for b in bars]
    assert timestamps == sorted(timestamps)


# ── fetch_quote ───────────────────────────────────────────────────────────────

def test_fetch_quote_returns_quote():
    from backend.app.services.yfinance_client import fetch_quote
    fast_info = MagicMock()
    fast_info.last_price = 189.37
    fast_info.previous_close = 187.0
    fast_info.three_month_average_volume = 55_000_000
    fast_info.market_cap = 2_900_000_000_000
    full_info = {
        "longName": "Apple Inc.", "currentPrice": 189.37,
        "previousClose": 187.0, "volume": 55_000_000,
        "marketCap": 2_900_000_000_000,
    }
    mock_ticker = MagicMock()
    mock_ticker.fast_info = fast_info
    mock_ticker.info = full_info
    with patch("backend.app.services.yfinance_client.yf.Ticker", return_value=mock_ticker):
        quote = fetch_quote("AAPL")
    assert quote is not None
    assert quote.ticker == "AAPL"
    assert quote.price == 189.37
    assert quote.asset_type == "stock"


def test_fetch_quote_detects_crypto():
    from backend.app.services.yfinance_client import fetch_quote
    fast_info = MagicMock()
    fast_info.last_price = 65000.0
    fast_info.previous_close = 64000.0
    fast_info.three_month_average_volume = 1000.0
    fast_info.market_cap = None
    full_info = {"longName": "Bitcoin USD", "currentPrice": 65000.0, "previousClose": 64000.0}
    mock_ticker = MagicMock()
    mock_ticker.fast_info = fast_info
    mock_ticker.info = full_info
    with patch("backend.app.services.yfinance_client.yf.Ticker", return_value=mock_ticker):
        quote = fetch_quote("BTC-USD")
    assert quote is not None
    assert quote.asset_type == "crypto"


def test_fetch_quote_returns_none_on_error():
    from backend.app.services.yfinance_client import fetch_quote
    with patch("backend.app.services.yfinance_client.yf.Ticker", side_effect=Exception("fail")):
        assert fetch_quote("AAPL") is None


# ── fetch_news ────────────────────────────────────────────────────────────────

def test_fetch_news_returns_items():
    from backend.app.services.yfinance_client import fetch_news
    raw = [
        {"title": "Apple beats estimates", "link": "http://x.com/1",
         "publisher": "Reuters", "providerPublishTime": 1700000000, "summary": ""},
        {"title": "iPhone demand strong", "link": "http://x.com/2",
         "publisher": "Bloomberg", "providerPublishTime": 1700000100, "summary": ""},
    ]
    mock_ticker = MagicMock()
    mock_ticker.news = raw
    with patch("backend.app.services.yfinance_client.yf.Ticker", return_value=mock_ticker):
        items = fetch_news("AAPL")
    assert len(items) == 2
    assert items[0].title == "Apple beats estimates"
    assert items[0].publisher == "Reuters"


def test_fetch_news_returns_empty_on_error():
    from backend.app.services.yfinance_client import fetch_news
    with patch("backend.app.services.yfinance_client.yf.Ticker", side_effect=Exception("fail")):
        assert fetch_news("AAPL") == []


# ── search_tickers ────────────────────────────────────────────────────────────

def test_search_tickers_returns_results():
    from backend.app.services.yfinance_client import search_tickers
    mock_results = MagicMock()
    mock_results.quotes = [
        {"symbol": "AAPL", "longname": "Apple Inc.", "quoteType": "EQUITY"},
        {"symbol": "AAPLX", "longname": "Apple Fund", "quoteType": "MUTUALFUND"},
    ]
    with patch("backend.app.services.yfinance_client.yf.Search", return_value=mock_results):
        results = search_tickers("apple")
    assert len(results) == 2
    assert results[0]["ticker"] == "AAPL"


def test_search_tickers_returns_empty_on_error():
    from backend.app.services.yfinance_client import search_tickers
    with patch("backend.app.services.yfinance_client.yf.Search", side_effect=Exception("fail")):
        assert search_tickers("apple") == []
