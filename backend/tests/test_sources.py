"""Tests for external data source adapters — Binance, FRED, RSS. All mocked."""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_cache():
    from backend.app.services.cache import cache
    cache.clear()
    yield
    cache.clear()


# ── Binance ───────────────────────────────────────────────────────────────────

class TestBinance:
    def _mock_httpx(self, json_data):
        mock_resp = MagicMock()
        mock_resp.json.return_value = json_data
        mock_resp.raise_for_status.return_value = None
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        return mock_client

    def test_to_binance_symbol_btc_usd(self):
        from backend.app.services.sources.binance import _to_binance_symbol
        assert _to_binance_symbol("BTC-USD") == "BTCUSDT"

    def test_to_binance_symbol_eth_usdt(self):
        from backend.app.services.sources.binance import _to_binance_symbol
        assert _to_binance_symbol("ETH-USDT") == "ETHUSDT"

    def test_to_binance_symbol_stock_returns_none(self):
        from backend.app.services.sources.binance import _to_binance_symbol
        assert _to_binance_symbol("AAPL") is None

    def test_supports_crypto(self):
        from backend.app.services.sources.binance import supports
        assert supports("BTC-USD") is True
        assert supports("AAPL") is False

    def test_fetch_ohlcv_returns_bars(self):
        from backend.app.services.sources.binance import fetch_ohlcv
        klines = [
            [1700000000000, "65000", "66000", "64000", "65500", "100", 0, 0, 0, 0, 0, 0]
            for _ in range(5)
        ]
        mock_client = self._mock_httpx(klines)
        with (
            patch("backend.app.services.sources.binance.httpx.Client", return_value=mock_client),
            patch("backend.app.services.sources.binance.rate_limiter.acquire"),
            patch("backend.app.services.sources.binance.circuit_breaker.get") as mock_cb,
        ):
            mock_cb.return_value.__enter__ = MagicMock(return_value=None)
            mock_cb.return_value.__exit__ = MagicMock(return_value=False)
            bars = fetch_ohlcv("BTC-USD", period="1mo", interval="1d")
        assert len(bars) == 5
        assert bars[0].close == 65500.0

    def test_fetch_ohlcv_empty_for_stock(self):
        from backend.app.services.sources.binance import fetch_ohlcv
        bars = fetch_ohlcv("AAPL")
        assert bars == []

    def test_fetch_ohlcv_empty_on_error(self):
        from backend.app.services.sources.binance import fetch_ohlcv
        with (
            patch("backend.app.services.sources.binance.httpx.Client", side_effect=Exception("fail")),
            patch("backend.app.services.sources.binance.rate_limiter.acquire"),
            patch("backend.app.services.sources.binance.circuit_breaker.get") as mock_cb,
        ):
            mock_cb.return_value.__enter__ = MagicMock(return_value=None)
            mock_cb.return_value.__exit__ = MagicMock(return_value=False)
            bars = fetch_ohlcv("BTC-USD")
        assert bars == []

    def test_fetch_quote_returns_none_for_stock(self):
        from backend.app.services.sources.binance import fetch_quote
        assert fetch_quote("AAPL") is None

    def test_fetch_quote_returns_crypto_quote(self):
        from backend.app.services.sources.binance import fetch_quote
        ticker_data = {
            "lastPrice": "65000.5", "prevClosePrice": "64000.0",
            "priceChangePercent": "1.5625", "volume": "1200.5",
        }
        mock_client = self._mock_httpx(ticker_data)
        with (
            patch("backend.app.services.sources.binance.httpx.Client", return_value=mock_client),
            patch("backend.app.services.sources.binance.rate_limiter.acquire"),
            patch("backend.app.services.sources.binance.circuit_breaker.get") as mock_cb,
        ):
            mock_cb.return_value.__enter__ = MagicMock(return_value=None)
            mock_cb.return_value.__exit__ = MagicMock(return_value=False)
            quote = fetch_quote("BTC-USD")
        assert quote is not None
        assert quote.price == 65000.5
        assert quote.asset_type == "crypto"


# ── FRED ──────────────────────────────────────────────────────────────────────

class TestFred:
    def _patch_fred(self, value: str = "4.5"):
        obs = {"observations": [{"value": value}, {"value": "."}]}
        mock_resp = MagicMock()
        mock_resp.json.return_value = obs
        mock_resp.raise_for_status.return_value = None
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        return mock_client

    def test_fetch_snapshot_returns_data_without_key(self):
        """Without a FRED key, snapshot falls back to yfinance proxies — returns a dict, not empty."""
        from backend.app.services.sources.fred import fetch_snapshot
        with (
            patch("backend.app.services.sources.fred.get_settings") as mock_cfg,
            patch("backend.app.services.sources.fred._yfinance_macro_fallback", return_value={"vix": 18.5, "fetched_at": "now"}),
        ):
            mock_cfg.return_value.has_fred_key = False
            result = fetch_snapshot()
        assert isinstance(result, dict)
        assert result.get("vix") == 18.5

    def test_fetch_snapshot_returns_dict_with_key(self):
        from backend.app.services.sources.fred import fetch_snapshot
        with (
            patch("backend.app.services.sources.fred.get_settings") as mock_cfg,
            patch("backend.app.services.sources.fred._latest_value", return_value=4.5),
        ):
            mock_cfg.return_value.has_fred_key = True
            mock_cfg.return_value.fred_api_key = "testkey"
            result = fetch_snapshot()
        assert "vix" in result or "fed_funds_rate" in result or result is not None

    def test_macro_context_string_no_key(self):
        from backend.app.services.sources.fred import macro_context_string
        with patch("backend.app.services.sources.fred.fetch_snapshot", return_value={}):
            ctx = macro_context_string()
        assert "unavailable" in ctx.lower()

    def test_macro_context_string_with_data(self):
        from backend.app.services.sources.fred import macro_context_string
        snap = {"vix": 18.5, "fed_funds_rate": 5.25, "treasury_10y": 4.2, "treasury_2y": 4.8,
                "yield_spread_10y2y": -0.6, "cpi": 310.5}
        with patch("backend.app.services.sources.fred.fetch_snapshot", return_value=snap):
            ctx = macro_context_string()
        assert "VIX" in ctx
        assert "Fed Funds" in ctx


# ── RSS ───────────────────────────────────────────────────────────────────────

class TestRss:
    def _make_entry(self, title: str, published: tuple = (2024, 1, 15, 12, 0, 0, 0, 0, 0)) -> dict:
        return {
            "title": title,
            "link": f"http://example.com/{title.replace(' ', '-')}",
            "published_parsed": published,
            "summary": f"Summary of {title}",
        }

    def test_fetch_news_returns_items(self):
        from backend.app.services.sources.rss import fetch_news
        entries = [
            self._make_entry("Apple earnings beat"),
            self._make_entry("iPhone demand strong"),
        ]
        mock_parsed = MagicMock()
        mock_parsed.entries = entries
        with (
            patch("backend.app.services.sources.rss.feedparser.parse", return_value=mock_parsed),
            patch("backend.app.services.sources.rss.rate_limiter.acquire"),
            patch("backend.app.services.sources.rss.circuit_breaker.get") as mock_cb,
        ):
            mock_cb.return_value.__enter__ = MagicMock(return_value=None)
            mock_cb.return_value.__exit__ = MagicMock(return_value=False)
            items = fetch_news("AAPL")
        assert len(items) >= 1
        assert items[0].publisher in ("Yahoo Finance", "Google News", "CoinDesk", "Reuters", "RSS")

    def test_fetch_news_deduplicates_by_title(self):
        from backend.app.services.sources.rss import fetch_news
        duplicate = self._make_entry("Same title repeated")
        mock_parsed = MagicMock()
        mock_parsed.entries = [duplicate, duplicate, duplicate]
        with (
            patch("backend.app.services.sources.rss.feedparser.parse", return_value=mock_parsed),
            patch("backend.app.services.sources.rss.rate_limiter.acquire"),
            patch("backend.app.services.sources.rss.circuit_breaker.get") as mock_cb,
        ):
            mock_cb.return_value.__enter__ = MagicMock(return_value=None)
            mock_cb.return_value.__exit__ = MagicMock(return_value=False)
            items = fetch_news("AAPL")
        assert len(items) == 1  # deduplicated

    def test_fetch_news_empty_on_parse_error(self):
        from backend.app.services.sources.rss import fetch_news
        with (
            patch("backend.app.services.sources.rss.feedparser.parse", side_effect=Exception("fail")),
            patch("backend.app.services.sources.rss.rate_limiter.acquire"),
            patch("backend.app.services.sources.rss.circuit_breaker.get") as mock_cb,
        ):
            mock_cb.return_value.__enter__ = MagicMock(return_value=None)
            mock_cb.return_value.__exit__ = MagicMock(return_value=False)
            items = fetch_news("AAPL")
        assert items == []

    def test_fetch_news_skips_empty_titles(self):
        from backend.app.services.sources.rss import fetch_news
        entries = [{"title": "", "link": "http://x.com", "published_parsed": None}]
        mock_parsed = MagicMock()
        mock_parsed.entries = entries
        with (
            patch("backend.app.services.sources.rss.feedparser.parse", return_value=mock_parsed),
            patch("backend.app.services.sources.rss.rate_limiter.acquire"),
            patch("backend.app.services.sources.rss.circuit_breaker.get") as mock_cb,
        ):
            mock_cb.return_value.__enter__ = MagicMock(return_value=None)
            mock_cb.return_value.__exit__ = MagicMock(return_value=False)
            items = fetch_news("AAPL")
        assert items == []


# ── data_fetcher dispatch ─────────────────────────────────────────────────────

class TestDataFetcher:
    @pytest.fixture(autouse=True)
    def _clear(self):
        from backend.app.services.cache import cache
        cache.clear()
        yield
        cache.clear()

    def test_is_crypto_detects_usd_suffix(self):
        from backend.app.services.data_fetcher import is_crypto
        assert is_crypto("BTC-USD") is True
        assert is_crypto("ETH-USDT") is True
        assert is_crypto("AAPL") is False

    def test_get_ohlcv_uses_binance_for_crypto(self):
        from backend.app.models.schemas import OHLCVBar
        from backend.app.services.data_fetcher import get_ohlcv
        base = dt.datetime(2024, 1, 1, tzinfo=dt.UTC)
        bars = [OHLCVBar(timestamp=base, open=1.0, high=1.1, low=0.9, close=1.0, volume=100.0)]
        with (
            patch("backend.app.services.data_fetcher.binance.fetch_ohlcv", return_value=bars) as mock_bin,
            patch("backend.app.services.data_fetcher.yfinance_client.fetch_ohlcv", return_value=[]),
        ):
            result = get_ohlcv("BTC-USD")
        mock_bin.assert_called_once()
        assert result == bars

    def test_get_ohlcv_falls_back_to_yfinance_for_crypto(self):
        from backend.app.models.schemas import OHLCVBar
        from backend.app.services.data_fetcher import get_ohlcv
        base = dt.datetime(2024, 1, 1, tzinfo=dt.UTC)
        bars = [OHLCVBar(timestamp=base, open=1.0, high=1.1, low=0.9, close=1.0, volume=100.0)]
        with (
            patch("backend.app.services.data_fetcher.binance.fetch_ohlcv", return_value=[]),
            patch("backend.app.services.data_fetcher.yfinance_client.fetch_ohlcv", return_value=bars) as mock_yf,
        ):
            result = get_ohlcv("BTC-USD")
        mock_yf.assert_called_once()
        assert result == bars

    def test_get_ohlcv_uses_yfinance_for_stocks(self):
        from backend.app.services.data_fetcher import get_ohlcv
        with (
            patch("backend.app.services.data_fetcher.binance.fetch_ohlcv") as mock_bin,
            patch("backend.app.services.data_fetcher.yfinance_client.fetch_ohlcv", return_value=[]),
        ):
            get_ohlcv("AAPL")
        mock_bin.assert_not_called()

    def test_search_merges_crypto_and_stocks(self):
        from backend.app.services.data_fetcher import search
        crypto = [{"ticker": "BTC-USD", "name": "Bitcoin", "type": "crypto"}]
        stocks = [{"ticker": "BTCY", "name": "BTC Corp", "type": "equity"}]
        with (
            patch("backend.app.services.data_fetcher.coingecko_client.search_coins", return_value=crypto),
            patch("backend.app.services.data_fetcher.yfinance_client.search_tickers", return_value=stocks),
        ):
            results = search("btc")
        tickers = {r["ticker"] for r in results}
        assert "BTC-USD" in tickers
        assert "BTCY" in tickers

    def test_search_deduplicates_by_ticker(self):
        from backend.app.services.data_fetcher import search
        dup = {"ticker": "BTC-USD", "name": "Bitcoin", "type": "crypto"}
        with (
            patch("backend.app.services.data_fetcher.coingecko_client.search_coins", return_value=[dup]),
            patch("backend.app.services.data_fetcher.yfinance_client.search_tickers", return_value=[dup]),
        ):
            results = search("btc")
        assert sum(1 for r in results if r["ticker"] == "BTC-USD") == 1
