"""
API endpoint coverage tests — hits every router with mocked service calls.
Uses a single TestClient fixture with scheduler disabled.
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.app.models.schemas import EnrichedNewsFeed, OHLCVBar, Quote

# ── Shared fixtures ───────────────────────────────────────────────────────────

def _bars(n: int = 60) -> list[OHLCVBar]:
    base = dt.datetime(2024, 1, 1, tzinfo=dt.UTC)
    price = 100.0
    bars = []
    for i in range(n):
        price += 0.2
        bars.append(OHLCVBar(
            timestamp=base + dt.timedelta(days=i),
            open=price - 0.1, high=price + 0.5, low=price - 0.5, close=price, volume=1_000_000.0,
        ))
    return bars


def _quote() -> Quote:
    return Quote(
        ticker="AAPL", name="Apple Inc.", price=189.37, change_1d=2.45,
        change_1d_pct=1.31, volume=55_000_000, asset_type="stock",
        last_updated=dt.datetime.now(dt.UTC),
    )


def _feed() -> EnrichedNewsFeed:
    return EnrichedNewsFeed(
        ticker="AAPL", items=[], rolling_sentiment=0.1, sentiment_label="slightly bullish",
    )


@pytest.fixture(scope="module")
def client():
    with (
        patch("backend.app.scheduler.start_scheduler"),
        patch("backend.app.scheduler.stop_scheduler"),
    ):
        from backend.app.database import init_db
        from backend.app.main import create_app
        init_db()
        with TestClient(create_app(), raise_server_exceptions=False) as c:
            yield c


# ── /health ───────────────────────────────────────────────────────────────────

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ── /assets/* ─────────────────────────────────────────────────────────────────

def test_search_assets(client):
    with patch("backend.app.api.assets.data_fetcher.search", return_value=[
        {"ticker": "AAPL", "name": "Apple Inc.", "type": "equity"}
    ]):
        r = client.get("/assets/search?q=apple")
    assert r.status_code == 200
    assert r.json()[0]["ticker"] == "AAPL"


def test_search_assets_requires_query(client):
    r = client.get("/assets/search")
    assert r.status_code == 422


def test_get_quote(client):
    with patch("backend.app.api.assets.data_fetcher.get_quote", return_value=_quote()):
        r = client.get("/assets/AAPL/quote")
    assert r.status_code == 200
    assert r.json()["price"] == 189.37


def test_get_quote_404(client):
    with patch("backend.app.api.assets.data_fetcher.get_quote", return_value=None):
        r = client.get("/assets/ZZZZZZ/quote")
    assert r.status_code == 404


def test_get_ohlcv(client):
    with patch("backend.app.api.assets.data_fetcher.get_ohlcv", return_value=_bars()):
        r = client.get("/assets/AAPL/ohlcv")
    assert r.status_code == 200
    assert len(r.json()) == 60


def test_get_ohlcv_404(client):
    with patch("backend.app.api.assets.data_fetcher.get_ohlcv", return_value=[]):
        r = client.get("/assets/ZZZZZZ/ohlcv")
    assert r.status_code == 404


def test_get_indicators(client):
    from backend.app.services.indicators import compute_indicators
    bars = _bars()
    snap = compute_indicators("AAPL", bars)
    with (
        patch("backend.app.api.assets.data_fetcher.get_ohlcv", return_value=bars),
        patch("backend.app.api.assets.compute_indicators", return_value=snap),
    ):
        r = client.get("/assets/AAPL/indicators")
    assert r.status_code == 200
    assert r.json()["ticker"] == "AAPL"


def test_get_indicators_insufficient_data(client):
    with (
        patch("backend.app.api.assets.data_fetcher.get_ohlcv", return_value=_bars(5)),
        patch("backend.app.api.assets.compute_indicators", return_value=None),
    ):
        r = client.get("/assets/AAPL/indicators")
    assert r.status_code == 422


# ── /assets/{ticker}/pulse-score ─────────────────────────────────────────────

def test_get_pulse_score(client):
    from backend.app.services.indicators import compute_indicators
    from backend.app.services.pulse_score import compute_pulse_score
    bars = _bars()
    snap = compute_indicators("AAPL", bars)
    score = compute_pulse_score("AAPL", bars, snap, None)
    feed = _feed()
    with (
        patch("backend.app.api.score.data_fetcher.get_ohlcv", return_value=bars),
        patch("backend.app.api.score.compute_indicators", return_value=snap),
        patch("backend.app.api.score.get_news_feed", return_value=feed),
        patch("backend.app.api.score.compute_pulse_score", return_value=score),
        patch("backend.app.api.score.compute_multi_timeframe", return_value=None),
    ):
        r = client.get("/assets/AAPL/pulse-score?include_ai=false&include_mtf=false")
    assert r.status_code == 200
    data = r.json()
    assert "overall" in data
    assert "scoring_version" in data


def test_pulse_score_weights_endpoint(client):
    r = client.get("/assets/AAPL/pulse-score/weights")
    assert r.status_code == 200
    data = r.json()
    assert "version" in data
    assert "components" in data


# ── /assets/{ticker}/news ─────────────────────────────────────────────────────

def test_get_news_404_no_items(client):
    empty_feed = EnrichedNewsFeed(
        ticker="AAPL", items=[], rolling_sentiment=0.0, sentiment_label="neutral"
    )
    with patch("backend.app.api.news.get_news_feed", return_value=empty_feed):
        r = client.get("/assets/AAPL/news")
    assert r.status_code == 404


# ── /macro/* ──────────────────────────────────────────────────────────────────

def test_macro_snapshot_no_key(client):
    # lazy import inside endpoint — patch the source module directly
    with patch("backend.app.services.sources.fred.fetch_snapshot", return_value={}):
        r = client.get("/macro/snapshot")
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_macro_context(client):
    with patch("backend.app.services.sources.fred.macro_context_string", return_value="VIX=18.5"):
        r = client.get("/macro/context")
    assert r.status_code == 200
    assert "context" in r.json()


# ── /watchlist ────────────────────────────────────────────────────────────────

def test_watchlist_crud(client):
    # Use a ticker that won't conflict with the default seed list
    TEST_TICKER = "TESTXYZ"
    # Add
    r = client.post("/watchlist/", json={"ticker": TEST_TICKER, "name": "Test Corp", "asset_type": "stock"})
    assert r.status_code == 201
    # List
    r = client.get("/watchlist/")
    assert r.status_code == 200
    tickers = [item["ticker"] for item in r.json()]
    assert TEST_TICKER in tickers
    # Delete
    r = client.delete(f"/watchlist/{TEST_TICKER}")
    assert r.status_code == 204
    # Confirm deleted
    r = client.get("/watchlist/")
    assert TEST_TICKER not in [item["ticker"] for item in r.json()]


def test_watchlist_duplicate_returns_409(client):
    client.post("/watchlist/", json={"ticker": "DUPTEST", "name": "", "asset_type": "stock"})
    r = client.post("/watchlist/", json={"ticker": "DUPTEST", "name": "", "asset_type": "stock"})
    assert r.status_code == 409
    client.delete("/watchlist/DUPTEST")


def test_watchlist_delete_nonexistent_returns_404(client):
    r = client.delete("/watchlist/NOSUCHTICKERXYZ")
    assert r.status_code == 404


# ── /alerts ───────────────────────────────────────────────────────────────────

def test_alerts_crud(client):
    # Create
    r = client.post("/alerts/", json={"ticker": "AAPL", "alert_type": "price_above", "threshold": 200.0})
    assert r.status_code == 201
    alert_id = r.json()["id"]
    # List
    r = client.get("/alerts/")
    assert r.status_code == 200
    # Toggle
    r = client.patch(f"/alerts/{alert_id}/toggle")
    assert r.status_code == 200
    assert r.json()["is_active"] is False
    # Toggle back
    r = client.patch(f"/alerts/{alert_id}/toggle")
    assert r.json()["is_active"] is True
    # Delete
    r = client.delete(f"/alerts/{alert_id}")
    assert r.status_code == 204


def test_alert_toggle_404(client):
    r = client.patch("/alerts/99999/toggle")
    assert r.status_code == 404


def test_alert_delete_404(client):
    r = client.delete("/alerts/99999")
    assert r.status_code == 404


# ── /admin/* ──────────────────────────────────────────────────────────────────

def test_admin_usage(client):
    r = client.get("/admin/usage")
    assert r.status_code == 200
    data = r.json()
    assert "today" in data
    assert "daily_budget_usd" in data


def test_admin_budget(client):
    r = client.get("/admin/budget")
    assert r.status_code == 200
    data = r.json()
    assert "exceeded" in data


def test_admin_requests(client):
    r = client.get("/admin/requests")
    assert r.status_code == 200
    assert "circuit_breakers" in r.json()


def test_admin_jobs(client):
    r = client.get("/admin/jobs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ── /backtest ─────────────────────────────────────────────────────────────────

def test_backtest_start_returns_job_id(client):
    with patch("backend.app.api.backtest._backtest_task"):  # don't actually run it
        r = client.post("/backtest", json={
            "ticker": "AAPL", "from_date": "2023-01-01", "to_date": "2023-03-31"
        })
    assert r.status_code == 202
    assert "job_id" in r.json()


def test_backtest_invalid_date_range(client):
    r = client.post("/backtest", json={
        "ticker": "AAPL", "from_date": "2023-12-31", "to_date": "2023-01-01"
    })
    assert r.status_code == 422


def test_backtest_list(client):
    r = client.get("/backtest")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_backtest_get_not_found(client):
    r = client.get("/backtest/bt-000000")
    assert r.status_code == 404
