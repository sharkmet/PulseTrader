"""Tests for the indicators module. All computed against synthetic bars."""
from __future__ import annotations

from backend.app.services.indicators import compute_indicators, compute_technical_score


def test_compute_indicators_returns_snapshot(sample_bars):
    snap = compute_indicators("AAPL", sample_bars)
    assert snap is not None
    assert snap.ticker == "AAPL"
    assert snap.price > 0


def test_rsi_in_valid_range(sample_bars):
    snap = compute_indicators("AAPL", sample_bars)
    assert snap is not None
    if snap.rsi_14.value is not None:
        assert 0.0 <= snap.rsi_14.value <= 100.0


def test_sma20_populated(sample_bars):
    snap = compute_indicators("AAPL", sample_bars)
    assert snap is not None
    assert snap.sma_20.value is not None


def test_sma200_none_with_insufficient_data(sample_bars):
    """60 bars isn't enough for SMA200 — value should be None."""
    snap = compute_indicators("AAPL", sample_bars)
    assert snap is not None
    assert snap.sma_200.value is None


def test_macd_histogram_present(sample_bars):
    snap = compute_indicators("AAPL", sample_bars)
    assert snap is not None
    assert snap.macd_histogram.value is not None


def test_bollinger_upper_gt_lower(sample_bars):
    snap = compute_indicators("AAPL", sample_bars)
    assert snap is not None
    if snap.bb_upper.value and snap.bb_lower.value:
        assert snap.bb_upper.value > snap.bb_lower.value


def test_interpretation_strings_non_empty(sample_bars):
    snap = compute_indicators("AAPL", sample_bars)
    assert snap is not None
    assert len(snap.rsi_14.interpretation) > 0
    assert len(snap.macd_histogram.interpretation) > 0


def test_technical_score_in_range(sample_bars):
    snap = compute_indicators("AAPL", sample_bars)
    assert snap is not None
    score, detail = compute_technical_score(snap)
    assert 0.0 <= score <= 100.0
    assert len(detail) > 0


def test_insufficient_bars_returns_none():
    import datetime as dt

    from backend.app.models.schemas import OHLCVBar

    tiny_bars = [
        OHLCVBar(
            timestamp=dt.datetime(2024, 1, i + 1, tzinfo=dt.UTC),
            open=100.0, high=105.0, low=95.0, close=102.0, volume=1_000_000,
        )
        for i in range(10)
    ]
    result = compute_indicators("TEST", tiny_bars)
    assert result is None
