"""Tests for the TOML weight loader and formula v2 properties."""
from __future__ import annotations

import math
from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from backend.app.services.score_weights import (
    ScoringConfig,
    _validate_sums_to_one,
    get_scoring_config,
)

# ── Weight loader ─────────────────────────────────────────────────────────────

def test_get_scoring_config_returns_config():
    cfg = get_scoring_config()
    assert isinstance(cfg, ScoringConfig)


def test_version_is_set():
    cfg = get_scoring_config()
    assert cfg.version == "2.0.0"


def test_component_weights_sum_to_one():
    cfg = get_scoring_config()
    total = cfg.components.total()
    assert math.isclose(total, 1.0, abs_tol=0.001), f"Component weights sum to {total}"


def test_all_components_present():
    cfg = get_scoring_config()
    w = cfg.components
    for field in ("trend", "momentum", "volatility", "volume", "sentiment", "multi_timeframe", "ai"):
        assert getattr(w, field) > 0, f"Weight '{field}' must be positive"


def test_sub_weights_trend_sum_to_one():
    cfg = get_scoring_config()
    sub = cfg.sub("trend")
    assert sub, "Trend sub-weights must be defined"
    assert math.isclose(sum(sub.values()), 1.0, abs_tol=0.001)


def test_sub_weights_momentum_sum_to_one():
    cfg = get_scoring_config()
    sub = cfg.sub("momentum")
    assert math.isclose(sum(sub.values()), 1.0, abs_tol=0.001)


def test_sub_weights_volatility_sum_to_one():
    cfg = get_scoring_config()
    sub = cfg.sub("volatility")
    assert math.isclose(sum(sub.values()), 1.0, abs_tol=0.001)


def test_sub_weights_volume_sum_to_one():
    cfg = get_scoring_config()
    sub = cfg.sub("volume")
    assert math.isclose(sum(sub.values()), 1.0, abs_tol=0.001)


def test_validate_sums_to_one_passes_for_valid():
    _validate_sums_to_one({"a": 0.6, "b": 0.4}, "test")  # should not raise


def test_validate_sums_to_one_raises_for_invalid():
    with pytest.raises(ValueError, match="sum"):
        _validate_sums_to_one({"a": 0.5, "b": 0.4}, "test")  # sums to 0.9


def test_component_weights_as_dict():
    cfg = get_scoring_config()
    d = cfg.components.as_dict()
    assert set(d.keys()) == {"trend", "momentum", "volatility", "volume", "sentiment", "multi_timeframe", "ai"}


def test_defaults_used_when_toml_missing():
    """When TOML path doesn't exist, defaults should still produce valid config."""
    from backend.app.services.score_weights import _DEFAULTS, _load_toml
    with patch("backend.app.services.score_weights._TOML_PATH") as mock_path:
        mock_path.exists.return_value = False
        result = _load_toml()
    # Should equal defaults
    assert result == _DEFAULTS


# ── Formula v2 invariants ─────────────────────────────────────────────────────

def test_pulse_score_7_components(sample_bars):
    from backend.app.services.indicators import compute_indicators
    from backend.app.services.pulse_score import compute_pulse_score
    snap = compute_indicators("TEST", sample_bars)
    result = compute_pulse_score("TEST", sample_bars, snap, None)
    # All 7 components must be present
    assert hasattr(result, "trend")
    assert hasattr(result, "momentum")
    assert hasattr(result, "volatility")
    assert hasattr(result, "volume")
    assert hasattr(result, "sentiment")
    assert hasattr(result, "multi_timeframe")
    assert hasattr(result, "ai")


def test_pulse_score_components_in_range(sample_bars):
    from backend.app.services.indicators import compute_indicators
    from backend.app.services.pulse_score import compute_pulse_score
    snap = compute_indicators("TEST", sample_bars)
    result = compute_pulse_score("TEST", sample_bars, snap, None)
    for field in ("trend", "momentum", "volatility", "volume", "sentiment", "multi_timeframe", "ai"):
        comp = getattr(result, field)
        assert 0.0 <= comp.score <= 100.0, f"{field}.score out of range: {comp.score}"


def test_rising_prices_score_higher_than_falling():
    """A consistently rising price series should score higher than a falling one."""
    import datetime as dt

    from backend.app.models.schemas import OHLCVBar
    from backend.app.services.indicators import compute_indicators
    from backend.app.services.pulse_score import compute_pulse_score

    base = dt.datetime(2023, 1, 1, tzinfo=dt.UTC)

    def make_bars(closes):
        return [
            OHLCVBar(
                timestamp=base + dt.timedelta(days=i),
                open=c * 0.995, high=c * 1.01, low=c * 0.99, close=c, volume=1_000_000.0
            )
            for i, c in enumerate(closes)
        ]

    rising_bars = make_bars([100 + i * 0.5 for i in range(60)])
    falling_bars = make_bars([130 - i * 0.5 for i in range(60)])

    snap_r = compute_indicators("RISING", rising_bars)
    snap_f = compute_indicators("FALLING", falling_bars)

    score_r = compute_pulse_score("RISING", rising_bars, snap_r, None, ai_score=50.0)
    score_f = compute_pulse_score("FALLING", falling_bars, snap_f, None, ai_score=50.0)

    assert score_r.overall > score_f.overall


@given(ai=st.floats(min_value=0, max_value=100))
@hyp_settings(max_examples=20, deadline=5000, suppress_health_check=["function_scoped_fixture"])
def test_overall_always_in_range_any_ai_score(ai, sample_bars):
    from backend.app.services.indicators import compute_indicators
    from backend.app.services.pulse_score import compute_pulse_score
    snap = compute_indicators("TEST", sample_bars)
    result = compute_pulse_score("TEST", sample_bars, snap, None, ai_score=ai)
    assert 0.0 <= result.overall <= 100.0
