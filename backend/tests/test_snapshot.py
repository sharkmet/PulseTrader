"""
Phase 7 tests — full snapshot endpoint, request middleware, loguru setup.
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.app.models.schemas import AssetSnapshot, OHLCVBar, Quote

# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_bars(n: int = 60) -> list[OHLCVBar]:
    base = dt.datetime(2024, 1, 1, tzinfo=dt.UTC)
    return [
        OHLCVBar(
            timestamp=base + dt.timedelta(days=i),
            open=100.0, high=102.0, low=98.0, close=101.0 + i * 0.1, volume=1_000_000,
        )
        for i in range(n)
    ]


def _make_quote(ticker: str = "AAPL") -> Quote:
    return Quote(
        ticker=ticker, name="Apple Inc.", price=189.37, change_1d=2.45,
        change_1d_pct=1.31, volume=55234100, asset_type="stock",
        last_updated=dt.datetime.now(dt.UTC),
    )


# ── AssetSnapshot schema ──────────────────────────────────────────────────────

def test_asset_snapshot_empty_is_valid():
    snap = AssetSnapshot(
        ticker="AAPL",
        fetched_at=dt.datetime.now(dt.UTC),
    )
    assert snap.ticker == "AAPL"
    assert snap.quote is None
    assert snap.errors == []


def test_asset_snapshot_with_errors():
    snap = AssetSnapshot(
        ticker="TEST",
        fetched_at=dt.datetime.now(dt.UTC),
        errors=["Quote unavailable: timeout", "FRED unavailable"],
    )
    assert len(snap.errors) == 2


def test_asset_snapshot_asset_type_default():
    snap = AssetSnapshot(ticker="AAPL", fetched_at=dt.datetime.now(dt.UTC))
    assert snap.asset_type == "stock"


# ── Snapshot endpoint (mocked) ────────────────────────────────────────────────

@pytest.fixture()
def test_client(sample_bars):
    """TestClient with external calls mocked. Pulse score runs for real."""
    quote = _make_quote()
    from backend.app.models.schemas import NewsFeed
    empty_feed = NewsFeed(ticker="AAPL", items=[], rolling_sentiment=0.0, sentiment_label="neutral")

    with (
        patch("backend.app.api.snapshot.data_fetcher.get_ohlcv", return_value=sample_bars),
        patch("backend.app.api.snapshot.data_fetcher.get_quote", return_value=quote),
        patch("backend.app.api.snapshot.get_news_feed", return_value=empty_feed),
        # compute_indicators + compute_pulse_score run for real with sample_bars
    ):
        from backend.app.main import create_app
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            yield client


def test_snapshot_returns_200(test_client):
    resp = test_client.get("/assets/AAPL?include_news=false")
    assert resp.status_code == 200


def test_snapshot_ticker_in_response(test_client):
    resp = test_client.get("/assets/AAPL?include_news=false")
    data = resp.json()
    assert data["ticker"] == "AAPL"


def test_snapshot_has_fetched_at(test_client):
    resp = test_client.get("/assets/AAPL?include_news=false")
    data = resp.json()
    assert "fetched_at" in data


def test_snapshot_404_for_missing_bars():
    with patch("backend.app.api.snapshot.data_fetcher.get_ohlcv", return_value=[]):
        from backend.app.main import create_app
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            resp = client.get("/assets/UNKNOWNTICKER999")
    assert resp.status_code == 404


# ── Request middleware ────────────────────────────────────────────────────────

def test_middleware_adds_request_id_header(test_client):
    resp = test_client.get("/health")
    assert "x-request-id" in resp.headers


def test_middleware_adds_response_time_header(test_client):
    resp = test_client.get("/health")
    assert "x-response-time-ms" in resp.headers
    latency = float(resp.headers["x-response-time-ms"])
    assert latency >= 0


def test_middleware_accepts_client_request_id(test_client):
    resp = test_client.get("/health", headers={"X-Request-ID": "my-custom-id"})
    assert resp.headers["x-request-id"] == "my-custom-id"


# ── Loguru setup ──────────────────────────────────────────────────────────────

def test_setup_loguru_does_not_raise():
    from backend.app.utils.logging_config import setup_loguru
    setup_loguru(level="WARNING")  # should not raise


def test_intercept_handler_routes_stdlib_logging():
    """stdlib logging.info() should not raise after loguru intercept is installed."""
    import logging

    from backend.app.utils.logging_config import setup_loguru
    setup_loguru(level="WARNING")
    log = logging.getLogger("test_intercept")
    log.info("this should not raise even though it goes through loguru")


# ── OpenAPI schema ────────────────────────────────────────────────────────────

def test_openapi_schema_has_quote_example(test_client):
    resp = test_client.get("/openapi.json")
    schema = resp.json()
    quote_schema = schema.get("components", {}).get("schemas", {}).get("Quote", {})
    assert "example" in quote_schema, "Quote schema should have an OpenAPI example"


def test_openapi_schema_includes_asset_snapshot(test_client):
    resp = test_client.get("/openapi.json")
    schema = resp.json()
    components = schema.get("components", {}).get("schemas", {})
    assert "AssetSnapshot" in components


def test_openapi_snapshot_endpoint_documented(test_client):
    resp = test_client.get("/openapi.json")
    schema = resp.json()
    paths = schema.get("paths", {})
    assert "/assets/{ticker}" in paths
    assert "get" in paths["/assets/{ticker}"]
