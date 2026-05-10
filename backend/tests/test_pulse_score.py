"""Tests for the Pulse Score engine — v2 formula (7 components)."""
from __future__ import annotations

from backend.app.services.indicators import compute_indicators
from backend.app.services.pulse_score import compute_pulse_score


def test_score_in_valid_range(sample_bars):
    snap = compute_indicators("AAPL", sample_bars)
    result = compute_pulse_score("AAPL", sample_bars, snap, None, ai_score=50.0)
    assert 0.0 <= result.overall <= 100.0


def test_score_breakdown_weights_sum_correctly(sample_bars):
    snap = compute_indicators("AAPL", sample_bars)
    result = compute_pulse_score("AAPL", sample_bars, snap, None, ai_score=50.0)
    total_weight = (
        result.trend.weight
        + result.momentum.weight
        + result.volatility.weight
        + result.volume.weight
        + result.sentiment.weight
        + result.multi_timeframe.weight
        + result.ai.weight
    )
    assert abs(total_weight - 1.0) < 0.001


def test_weighted_contributions_sum_to_overall(sample_bars):
    snap = compute_indicators("AAPL", sample_bars)
    result = compute_pulse_score("AAPL", sample_bars, snap, None, ai_score=50.0)
    expected = (
        result.trend.weighted_contribution
        + result.momentum.weighted_contribution
        + result.volatility.weighted_contribution
        + result.volume.weighted_contribution
        + result.sentiment.weighted_contribution
        + result.multi_timeframe.weighted_contribution
        + result.ai.weighted_contribution
    )
    assert abs(result.overall - expected) < 0.1


def test_no_news_defaults_to_neutral(sample_bars):
    snap = compute_indicators("AAPL", sample_bars)
    result = compute_pulse_score("AAPL", sample_bars, snap, None, ai_score=50.0)
    assert result.sentiment.score == 50.0


def test_ai_component_respected(sample_bars):
    snap = compute_indicators("AAPL", sample_bars)
    result_low = compute_pulse_score("AAPL", sample_bars, snap, None, ai_score=0.0)
    result_high = compute_pulse_score("AAPL", sample_bars, snap, None, ai_score=100.0)
    assert result_high.overall > result_low.overall


def test_score_label_assigned(sample_bars):
    snap = compute_indicators("AAPL", sample_bars)
    result = compute_pulse_score("AAPL", sample_bars, snap, None, ai_score=50.0)
    assert result.label in ("Strong", "Bullish", "Neutral", "Bearish", "Weak")


def test_detail_strings_non_empty(sample_bars):
    snap = compute_indicators("AAPL", sample_bars)
    result = compute_pulse_score("AAPL", sample_bars, snap, None, ai_score=50.0)
    assert len(result.trend.detail) > 0
    assert len(result.momentum.detail) > 0


def test_scoring_version_present(sample_bars):
    snap = compute_indicators("AAPL", sample_bars)
    result = compute_pulse_score("AAPL", sample_bars, snap, None, ai_score=50.0)
    assert result.scoring_version == "2.0.0"


def test_sub_scores_populated(sample_bars):
    snap = compute_indicators("AAPL", sample_bars)
    result = compute_pulse_score("AAPL", sample_bars, snap, None, ai_score=50.0)
    assert len(result.trend.sub_scores) > 0
    assert len(result.momentum.sub_scores) > 0


def test_data_sources_populated(sample_bars):
    snap = compute_indicators("AAPL", sample_bars)
    result = compute_pulse_score("AAPL", sample_bars, snap, None, ai_score=50.0)
    assert len(result.trend.data_sources) > 0
    assert len(result.momentum.data_sources) > 0


def test_multi_timeframe_neutral_when_not_provided(sample_bars):
    snap = compute_indicators("AAPL", sample_bars)
    result = compute_pulse_score("AAPL", sample_bars, snap, None, ai_score=50.0)
    # No MTF passed → neutral 50
    assert result.multi_timeframe.score == 50.0


def test_multi_timeframe_used_when_provided(sample_bars):
    from backend.app.services.indicators import compute_multi_timeframe
    snap = compute_indicators("AAPL", sample_bars)
    mtf = compute_multi_timeframe("AAPL", {"1d": sample_bars})
    result = compute_pulse_score("AAPL", sample_bars, snap, None, ai_score=50.0, mtf_snapshot=mtf)
    # MTF score should match the agreement score
    assert abs(result.multi_timeframe.score - mtf.agreement_score) < 0.01
