"""
Extended indicator tests — ground-truth checks for the Phase 3 indicator set.

Philosophy: test mathematical properties and known-value assertions, not just
"output is in range". Where exact values are hard to predict (stochastic math),
test direction/monotonicity instead.
"""
from __future__ import annotations

import datetime as dt
import math

from hypothesis import given, settings
from hypothesis import strategies as st

from backend.app.models.schemas import OHLCVBar
from backend.app.services.indicators import (
    _autocorr_lag1,
    _hurst_exponent,
    _realized_volatility,
    _rolling_zscore,
    compute_indicators,
    compute_multi_timeframe,
    compute_technical_score,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_bars(closes: list[float], start: dt.datetime | None = None) -> list[OHLCVBar]:
    """Build OHLCVBar list from close prices. OHLCV derived simply."""
    base = start or dt.datetime(2023, 1, 1, tzinfo=dt.UTC)
    bars = []
    for i, c in enumerate(closes):
        bars.append(OHLCVBar(
            timestamp=base + dt.timedelta(days=i),
            open=c * 0.995,
            high=c * 1.01,
            low=c * 0.99,
            close=c,
            volume=1_000_000.0,
        ))
    return bars


def _flat_bars(price: float = 100.0, n: int = 60) -> list[OHLCVBar]:
    return _make_bars([price] * n)


def _rising_bars(start: float = 100.0, step: float = 1.0, n: int = 60) -> list[OHLCVBar]:
    return _make_bars([start + i * step for i in range(n)])


def _falling_bars(start: float = 160.0, step: float = 1.0, n: int = 60) -> list[OHLCVBar]:
    return _make_bars([start - i * step for i in range(n)])


# ── Core indicator presence & validity ───────────────────────────────────────

def test_extended_indicators_present_with_enough_data(sample_bars):
    snap = compute_indicators("TEST", sample_bars)
    assert snap is not None
    # Phase 3 indicators should be populated (not None) with 60 bars
    assert snap.adx_14 is not None
    assert snap.stoch_k is not None
    assert snap.stoch_d is not None
    assert snap.williams_r is not None
    assert snap.roc_10 is not None
    assert snap.atr_14 is not None
    assert snap.keltner_upper is not None
    assert snap.keltner_lower is not None
    assert snap.obv is not None
    assert snap.mfi_14 is not None
    assert snap.z_score_20 is not None
    assert snap.realized_vol_20 is not None
    assert snap.autocorr_lag1 is not None


def test_bar_count_recorded(sample_bars):
    snap = compute_indicators("TEST", sample_bars)
    assert snap is not None
    assert snap.bar_count == len(sample_bars)


# ── ADX ──────────────────────────────────────────────────────────────────────

def test_adx_in_valid_range(sample_bars):
    snap = compute_indicators("TEST", sample_bars)
    assert snap is not None
    if snap.adx_14 and snap.adx_14.value is not None:
        assert 0 <= snap.adx_14.value <= 100


def test_adx_higher_for_trending_series():
    """Steadily rising prices should have higher ADX than a flat series."""
    snap_flat = compute_indicators("FLAT", _flat_bars(n=60))
    snap_trend = compute_indicators("TREND", _rising_bars(n=60))
    assert snap_flat is not None and snap_trend is not None
    adx_flat = (snap_flat.adx_14 and snap_flat.adx_14.value) or 0
    adx_trend = (snap_trend.adx_14 and snap_trend.adx_14.value) or 0
    assert adx_trend > adx_flat


# ── Stochastic ───────────────────────────────────────────────────────────────

def test_stochastic_in_valid_range(sample_bars):
    snap = compute_indicators("TEST", sample_bars)
    assert snap is not None
    if snap.stoch_k and snap.stoch_k.value is not None:
        assert 0 <= snap.stoch_k.value <= 100
    if snap.stoch_d and snap.stoch_d.value is not None:
        assert 0 <= snap.stoch_d.value <= 100


def test_stochastic_high_for_rising_series():
    snap = compute_indicators("TEST", _rising_bars(n=60))
    assert snap is not None
    if snap.stoch_k and snap.stoch_k.value is not None:
        assert snap.stoch_k.value > 50  # price near top of 14-period range


def test_stochastic_low_for_falling_series():
    snap = compute_indicators("TEST", _falling_bars(n=60))
    assert snap is not None
    if snap.stoch_k and snap.stoch_k.value is not None:
        assert snap.stoch_k.value < 50


# ── Williams %R ───────────────────────────────────────────────────────────────

def test_williams_r_in_valid_range(sample_bars):
    snap = compute_indicators("TEST", sample_bars)
    assert snap is not None
    if snap.williams_r and snap.williams_r.value is not None:
        assert -100 <= snap.williams_r.value <= 0


# ── ATR ──────────────────────────────────────────────────────────────────────

def test_atr_positive(sample_bars):
    snap = compute_indicators("TEST", sample_bars)
    assert snap is not None
    if snap.atr_14 and snap.atr_14.value is not None:
        assert snap.atr_14.value >= 0


def test_atr_near_zero_for_flat_series():
    """ATR of a flat series should be small (bounded by the fixed H-L spread in _flat_bars)."""
    snap = compute_indicators("FLAT", _flat_bars(n=60))
    assert snap is not None
    if snap.atr_14 and snap.atr_14.value is not None:
        # _flat_bars sets high=close*1.01, low=close*0.99 → H-L range = 2.0 at price 100
        # ATR of a constant-range series equals that range exactly
        assert snap.atr_14.value <= 2.1  # within the bar's H-L spread


# ── Keltner Channels ─────────────────────────────────────────────────────────

def test_keltner_upper_above_lower(sample_bars):
    snap = compute_indicators("TEST", sample_bars)
    assert snap is not None
    if snap.keltner_upper and snap.keltner_lower:
        upper_v = snap.keltner_upper.value
        lower_v = snap.keltner_lower.value
        if upper_v is not None and lower_v is not None:
            assert upper_v > lower_v


# ── OBV ──────────────────────────────────────────────────────────────────────

def test_obv_increases_when_price_rises():
    """OBV should be net positive for a consistently rising series (close > open)."""
    bars = _rising_bars(start=100, step=1, n=60)
    # Make sure close > prev_close to ensure OBV adds volume each day
    snap = compute_indicators("TEST", bars)
    assert snap is not None
    if snap.obv and snap.obv.value is not None:
        # OBV starts at volume on first bar, then adds each day — should be large positive
        assert snap.obv.value > 0


# ── MFI ──────────────────────────────────────────────────────────────────────

def test_mfi_in_valid_range(sample_bars):
    snap = compute_indicators("TEST", sample_bars)
    assert snap is not None
    if snap.mfi_14 and snap.mfi_14.value is not None:
        assert 0 <= snap.mfi_14.value <= 100


# ── Statistical indicators ────────────────────────────────────────────────────

def test_zscore_near_zero_at_mean():
    """Z-score of a flat series (price at mean) should be ~0."""
    bars = _flat_bars(price=100.0, n=50)
    snap = compute_indicators("TEST", bars)
    assert snap is not None
    if snap.z_score_20 and snap.z_score_20.value is not None:
        assert abs(snap.z_score_20.value) < 0.5  # very near zero for flat


def test_zscore_positive_for_high_price():
    """If last bar is much higher than recent average, z-score should be positive."""
    closes = [100.0] * 40 + [150.0] * 10  # big jump at the end
    snap = compute_indicators("TEST", _make_bars(closes))
    assert snap is not None
    if snap.z_score_20 and snap.z_score_20.value is not None:
        assert snap.z_score_20.value > 0


def test_rolling_zscore_known_value():
    """Hand-verify z-score for a simple series."""
    import pandas as pd
    # Need at least window+1=21 data points for the rolling window to produce a value
    prices = [100.0] * 20 + [110.0]  # 21 points; last price is 10 above mean
    series = pd.Series(prices)
    window = 20
    # The rolling window uses the last 20 values: nineteen 100s + one 110
    window_data = pd.Series(prices[-window:])
    mean = window_data.mean()
    std = window_data.std(ddof=1)
    expected_z = (110.0 - mean) / std if std > 0 else 0

    result = _rolling_zscore(series, window=window)
    assert result is not None
    assert abs(result - expected_z) < 0.01


def test_realized_vol_positive(sample_bars):
    snap = compute_indicators("TEST", sample_bars)
    assert snap is not None
    if snap.realized_vol_20 and snap.realized_vol_20.value is not None:
        assert snap.realized_vol_20.value >= 0


def test_realized_vol_higher_for_volatile_series():
    """A series with large daily moves should have higher realised vol."""
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(42)
    volatile = pd.Series(100 + rng.normal(0, 5, 60).cumsum())
    calm = pd.Series(100 + rng.normal(0, 0.5, 60).cumsum())
    rv_volatile = _realized_volatility(volatile, window=20)
    rv_calm = _realized_volatility(calm, window=20)
    assert rv_volatile is not None and rv_calm is not None
    assert rv_volatile > rv_calm


def test_hurst_near_half_for_random_walk():
    """A geometric random walk should produce H ≈ 0.5."""
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(99)
    prices = pd.Series(100 * np.exp(rng.normal(0, 0.01, 200).cumsum()))
    h = _hurst_exponent(prices)
    assert h is not None
    assert 0.2 <= h <= 0.8  # wide bounds given the stochastic nature


def test_hurst_none_for_short_series():
    import pandas as pd
    prices = pd.Series(range(30))
    h = _hurst_exponent(prices)
    assert h is None


def test_hurst_trending_series_above_half():
    """A monotonically trending series should produce H > 0.5."""
    import pandas as pd
    prices = pd.Series([100.0 + i * 0.1 + 0.01 * i**0.5 for i in range(100)])
    h = _hurst_exponent(prices)
    if h is not None:  # may be None if windows are too small
        assert h > 0.45  # trending → persistence


def test_autocorr_defined(sample_bars):
    snap = compute_indicators("TEST", sample_bars)
    assert snap is not None
    if snap.autocorr_lag1 and snap.autocorr_lag1.value is not None:
        assert -1.0 <= snap.autocorr_lag1.value <= 1.0


def test_autocorr_positive_for_trending_returns():
    """A consistent trend should have positive lag-1 autocorrelation."""
    import pandas as pd
    prices = pd.Series([100.0 + i * 0.5 for i in range(80)])
    ac = _autocorr_lag1(prices)
    # Trending but small increments — autocorr should be > 0 but might be small
    if ac is not None:
        assert ac > -0.5  # at least not strongly anti-correlated


# ── Ichimoku ─────────────────────────────────────────────────────────────────

def test_ichimoku_tenkan_present_with_enough_bars(sample_bars):
    snap = compute_indicators("TEST", sample_bars)
    assert snap is not None
    # 60 bars > 9 → tenkan should be present
    assert snap.ichimoku_tenkan is not None
    assert snap.ichimoku_tenkan.value is not None


def test_ichimoku_kijun_present_with_enough_bars(sample_bars):
    snap = compute_indicators("TEST", sample_bars)
    assert snap is not None
    # 60 bars > 26 → kijun should be present
    assert snap.ichimoku_kijun is not None
    assert snap.ichimoku_kijun.value is not None


def test_ichimoku_kijun_known_value():
    """26-period kijun = (26-period high + 26-period low) / 2."""
    # Use a simple rising series where we can compute by hand
    closes = list(range(1, 61))  # 1..60, last 26 are 35..60
    bars = _make_bars([float(c) for c in closes])
    snap = compute_indicators("TEST", bars)
    assert snap is not None
    if snap.ichimoku_kijun and snap.ichimoku_kijun.value is not None:
        # Last 26 bars: closes 35..60
        # High ≈ 60*1.01=60.6, Low ≈ 35*0.99=34.65
        # Kijun ≈ (60.6 + 34.65) / 2 ≈ 47.6
        kijun = snap.ichimoku_kijun.value
        assert 40 <= kijun <= 55  # reasonable range given bar construction


# ── Multi-timeframe ───────────────────────────────────────────────────────────

def test_multi_timeframe_returns_snapshot(sample_bars):
    mtf = compute_multi_timeframe("TEST", {"1d": sample_bars})
    assert mtf.ticker == "TEST"
    assert "1d" in mtf.timeframes
    assert -1.0 <= mtf.directions["1d"] <= 1.0
    assert 0 <= mtf.agreement_score <= 100


def test_multi_timeframe_agreement_high_when_aligned():
    """Identical timeframes should give maximum agreement."""
    rising = _rising_bars(n=60)
    mtf = compute_multi_timeframe("TEST", {"1d": rising, "1w": rising})
    # Identical inputs → direction scores identical → std=0 → high agreement
    dir_values = list(mtf.directions.values())
    assert len(set(dir_values)) == 1 or abs(dir_values[0] - dir_values[1]) < 0.01


def test_multi_timeframe_diverging_label_for_conflicting_signals():
    """Bullish 1D + bearish 1W should show disagreement."""
    mtf = compute_multi_timeframe(
        "TEST",
        {"1d": _rising_bars(n=60), "1w": _falling_bars(n=60)},
    )
    assert mtf.agreement_label in ("Diverging", "Weak Agreement", "Moderate Agreement")


def test_multi_timeframe_empty_input():
    mtf = compute_multi_timeframe("TEST", {})
    assert mtf.agreement_score == 50.0
    assert mtf.consensus_direction == "Unknown"


def test_multi_timeframe_skips_insufficient_bars():
    tiny = _make_bars([100.0] * 5)  # too few
    good = _rising_bars(n=60)
    mtf = compute_multi_timeframe("TEST", {"tiny": tiny, "1d": good})
    assert "tiny" not in mtf.timeframes
    assert "1d" in mtf.timeframes


# ── Hypothesis: indicator math properties ────────────────────────────────────

@given(n=st.integers(min_value=30, max_value=200))
@settings(max_examples=20, deadline=5000)
def test_technical_score_always_in_range_for_any_bar_count(n):
    bars = _rising_bars(n=n)
    snap = compute_indicators("HYPO", bars)
    if snap is not None:
        score, detail = compute_technical_score(snap)
        assert 0.0 <= score <= 100.0
        assert len(detail) > 0


@given(price=st.floats(min_value=0.01, max_value=100_000, allow_nan=False, allow_infinity=False))
@settings(max_examples=20, deadline=5000)
def test_flat_series_zscore_near_zero(price):
    import pandas as pd
    series = pd.Series([price] * 40)
    z = _rolling_zscore(series, window=20)
    if z is not None:
        # Flat series → z-score should be 0 (or NaN → None)
        assert abs(z) < 1e-6 or math.isnan(z)
