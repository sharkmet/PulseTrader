"""
Indicator engine — computes technical + statistical indicators from OHLCV bars.

Standard indicators use the `ta` library.
Ichimoku Cloud, Hurst exponent, z-score, realized vol, and autocorrelation are
implemented here directly (ta doesn't cover them).

Each indicator returns an IndicatorValue(value, interpretation) pair.
None values mean insufficient data rather than a failed computation.
"""
from __future__ import annotations

import logging
import math
import statistics
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import ta

from backend.app.models.schemas import (
    IndicatorSnapshot,
    IndicatorValue,
    MultiTimeframeSnapshot,
    OHLCVBar,
)

logger = logging.getLogger(__name__)

_MIN_BARS = 30  # absolute minimum to compute anything useful


# ── Interpretation helpers ────────────────────────────────────────────────────

def _interp_rsi(v: float) -> str:
    if v < 20: return f"RSI {v:.1f}: Extremely oversold — high reversal potential"
    if v < 30: return f"RSI {v:.1f}: Oversold — potential bounce zone"
    if v < 45: return f"RSI {v:.1f}: Below midpoint — mild bearish bias"
    if v < 55: return f"RSI {v:.1f}: Neutral"
    if v < 70: return f"RSI {v:.1f}: Bullish momentum building"
    if v < 80: return f"RSI {v:.1f}: Overbought — watch for pullback"
    return f"RSI {v:.1f}: Extremely overbought — elevated reversal risk"


def _interp_macd(hist: float, hist_prev: float) -> str:
    if hist > 0 and hist > hist_prev: return f"MACD hist {hist:.3f}: Bullish momentum accelerating"
    if hist > 0: return f"MACD hist {hist:.3f}: Bullish but momentum fading"
    if hist < 0 and hist < hist_prev: return f"MACD hist {hist:.3f}: Bearish momentum accelerating"
    if hist < 0: return f"MACD hist {hist:.3f}: Bearish momentum slowing"
    return f"MACD hist {hist:.3f}: Neutral"


def _interp_ma(price: float, ma: float, label: str) -> str:
    pct = (price - ma) / ma * 100
    direction = "above" if price >= ma else "below"
    return f"Price {direction} {label} by {abs(pct):.1f}%"


def _interp_bb(price: float, upper: float, lower: float) -> str:
    if upper == lower: return "Bands too narrow"
    pct_b = (price - lower) / (upper - lower)
    if pct_b > 1.0: return f"%B {pct_b:.2f}: Price above upper band — overbought stretch"
    if pct_b > 0.8: return f"%B {pct_b:.2f}: Near upper band — bullish"
    if pct_b > 0.5: return f"%B {pct_b:.2f}: Upper half — mild bullish bias"
    if pct_b > 0.2: return f"%B {pct_b:.2f}: Lower half — mild bearish bias"
    if pct_b > 0.0: return f"%B {pct_b:.2f}: Near lower band — oversold"
    return f"%B {pct_b:.2f}: Price below lower band — oversold stretch"


def _interp_volume(ratio: float) -> str:
    if ratio > 3.0: return f"Volume {ratio:.1f}x avg — extreme surge"
    if ratio > 2.0: return f"Volume {ratio:.1f}x avg — heavy activity, conviction move"
    if ratio > 1.3: return f"Volume {ratio:.1f}x avg — above average"
    if ratio > 0.7: return f"Volume {ratio:.1f}x avg — typical activity"
    return f"Volume {ratio:.1f}x avg — below average, low conviction"


def _interp_adx(v: float) -> str:
    if v < 15: return f"ADX {v:.1f}: No trend — market ranging"
    if v < 25: return f"ADX {v:.1f}: Weak trend developing"
    if v < 40: return f"ADX {v:.1f}: Strong trend in place"
    if v < 60: return f"ADX {v:.1f}: Very strong trend"
    return f"ADX {v:.1f}: Extreme trend strength"


def _interp_stoch(k: float, d: float) -> str:
    if k > 80 and d > 80: return f"Stoch K={k:.1f}/D={d:.1f}: Overbought — watch for reversal"
    if k < 20 and d < 20: return f"Stoch K={k:.1f}/D={d:.1f}: Oversold — potential bounce"
    if k > d: return f"Stoch K={k:.1f}/D={d:.1f}: Bullish crossover"
    return f"Stoch K={k:.1f}/D={d:.1f}: Bearish crossover"


def _interp_williams(v: float) -> str:
    if v > -20: return f"W%R {v:.1f}: Overbought (-20 threshold)"
    if v < -80: return f"W%R {v:.1f}: Oversold (-80 threshold)"
    return f"W%R {v:.1f}: Neutral zone"


def _interp_hurst(h: float) -> str:
    if h > 0.65: return f"H={h:.3f}: Strong trend persistence — momentum likely to continue"
    if h > 0.55: return f"H={h:.3f}: Mild trend persistence"
    if h > 0.45: return f"H={h:.3f}: Random walk — no predictable direction"
    if h > 0.35: return f"H={h:.3f}: Mild mean-reversion tendency"
    return f"H={h:.3f}: Strong mean-reversion — extended moves tend to reverse"


def _interp_ichimoku(price: float, tenkan: float, kijun: float) -> str:
    above_kijun = price > kijun
    tk_bullish = tenkan > kijun
    parts = []
    parts.append("Price above Kijun" if above_kijun else "Price below Kijun")
    parts.append("TK bullish" if tk_bullish else "TK bearish")
    return " | ".join(parts)


# ── Statistical computations ──────────────────────────────────────────────────

def _rolling_zscore(close: pd.Series, window: int = 20) -> float | None:
    if len(close) < window + 1:
        return None
    rolling_mean = close.rolling(window).mean()
    rolling_std = close.rolling(window).std(ddof=1)
    z = (close - rolling_mean) / rolling_std
    val = z.iloc[-1]
    return float(val) if pd.notna(val) else None


def _realized_volatility(close: pd.Series, window: int = 20) -> float | None:
    """Annualised realised volatility (%) over `window` trading days."""
    if len(close) < window + 1:
        return None
    log_ret = np.log(close / close.shift(1)).dropna()
    if len(log_ret) < window:
        return None
    rv = log_ret.iloc[-window:].std(ddof=1) * math.sqrt(252) * 100
    return round(float(rv), 2) if pd.notna(rv) else None


def _hurst_exponent(close: pd.Series) -> float | None:
    """
    R/S analysis Hurst exponent.
    H > 0.5: trending, H ≈ 0.5: random walk, H < 0.5: mean-reverting.
    Requires ≥ 50 observations.
    """
    n = len(close)
    if n < 50:
        return None

    log_ret = np.log(close / close.shift(1)).dropna().to_numpy()
    if len(log_ret) < 30:
        return None

    max_w = len(log_ret) // 2
    min_w = max(8, max_w // 8)
    n_steps = min(10, max_w - min_w)
    if n_steps < 2:
        return None

    windows = np.unique(np.linspace(min_w, max_w, n_steps, dtype=int))
    rs_pairs: list[tuple[int, float]] = []

    for w in windows:
        chunks = [log_ret[i : i + w] for i in range(0, len(log_ret) - w + 1, w)]
        rs_vals = []
        for chunk in chunks:
            if len(chunk) < 2:
                continue
            m = chunk.mean()
            cumdev = np.cumsum(chunk - m)
            r = cumdev.max() - cumdev.min()
            s = chunk.std(ddof=1)
            if s > 0:
                rs_vals.append(r / s)
        if rs_vals:
            rs_pairs.append((int(w), float(np.mean(rs_vals))))

    if len(rs_pairs) < 2:
        return None

    log_n = np.log([p[0] for p in rs_pairs])
    log_rs = np.log([p[1] for p in rs_pairs])
    slope = float(np.polyfit(log_n, log_rs, 1)[0])
    return round(slope, 3)


def _autocorr_lag1(close: pd.Series) -> float | None:
    """Lag-1 autocorrelation of log returns."""
    if len(close) < 10:
        return None
    log_ret = np.log(close / close.shift(1)).dropna()
    if len(log_ret) < 5:
        return None
    val = log_ret.autocorr(lag=1)
    return round(float(val), 3) if pd.notna(val) else None


# ── Ichimoku Cloud (manual — not in `ta`) ─────────────────────────────────────

def _ichimoku(high: pd.Series, low: pd.Series, close: pd.Series) -> dict[str, float | None]:
    n = len(close)

    def midpoint(h: pd.Series, lo: pd.Series, period: int) -> pd.Series:
        return (h.rolling(period).max() + lo.rolling(period).min()) / 2

    result: dict[str, float | None] = {
        "tenkan": None, "kijun": None,
        "senkou_a": None, "senkou_b": None,
    }
    if n >= 9:
        v = midpoint(high, low, 9).iloc[-1]
        result["tenkan"] = float(v) if pd.notna(v) else None
    if n >= 26:
        v = midpoint(high, low, 26).iloc[-1]
        result["kijun"] = float(v) if pd.notna(v) else None
        # Senkou A = (tenkan + kijun) / 2, plotted 26 periods forward
        # "Current" cloud = the spans computed 26 bars ago
        if n >= 35:  # need at least 9+26 = 35 bars to have a value 26 bars ago
            t26 = midpoint(high, low, 9).iloc[-27] if n > 26 else None
            k26 = midpoint(high, low, 26).iloc[-27] if n > 26 else None
            if t26 is not None and k26 is not None and pd.notna(t26) and pd.notna(k26):
                result["senkou_a"] = float((t26 + k26) / 2)
    if n >= 78:  # 52 + 26
        v = midpoint(high.iloc[:-26], low.iloc[:-26], 52).iloc[-1]
        result["senkou_b"] = float(v) if pd.notna(v) else None

    return result


# ── Main computation ──────────────────────────────────────────────────────────

def compute_indicators(ticker: str, bars: list[OHLCVBar]) -> IndicatorSnapshot | None:
    if len(bars) < _MIN_BARS:
        logger.warning("Insufficient bars for %s: %d (need ≥%d)", ticker, len(bars), _MIN_BARS)
        return None

    df = pd.DataFrame([
        {"open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
        for b in bars
    ])
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    price = float(close.iloc[-1])
    n = len(df)

    def _s(series: pd.Series) -> float | None:
        val = series.iloc[-1]
        return float(val) if pd.notna(val) else None

    def _iv(value: float | None, interp: str) -> IndicatorValue:
        return IndicatorValue(value=value, interpretation=interp)

    def _iv_opt(value: float | None, interp: str) -> IndicatorValue | None:
        return IndicatorValue(value=value, interpretation=interp) if value is not None else None

    # ── Trend ─────────────────────────────────────────────────────────────────
    sma20 = _s(ta.trend.SMAIndicator(close=close, window=20).sma_indicator())
    sma50 = _s(ta.trend.SMAIndicator(close=close, window=50).sma_indicator()) if n >= 50 else None
    sma200 = _s(ta.trend.SMAIndicator(close=close, window=200).sma_indicator()) if n >= 200 else None
    ema12 = _s(ta.trend.EMAIndicator(close=close, window=12).ema_indicator())
    ema26 = _s(ta.trend.EMAIndicator(close=close, window=26).ema_indicator())

    macd_ind = ta.trend.MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
    macd_line_v = _s(macd_ind.macd())
    macd_sig_v = _s(macd_ind.macd_signal())
    macd_hist_v = _s(macd_ind.macd_diff())
    hist_series = macd_ind.macd_diff().dropna()
    hist_prev = float(hist_series.iloc[-2]) if len(hist_series) >= 2 else 0.0

    adx_v: float | None = None
    if n >= 28:
        adx_v = _s(ta.trend.ADXIndicator(high=high, low=low, close=close, window=14).adx())

    ichimoku_vals = _ichimoku(high, low, close)

    # ── Momentum ──────────────────────────────────────────────────────────────
    rsi_v = _s(ta.momentum.RSIIndicator(close=close, window=14).rsi())

    stoch_k_v = stoch_d_v = None
    if n >= 20:
        stoch = ta.momentum.StochasticOscillator(high=high, low=low, close=close, window=14, smooth_window=3)
        stoch_k_v = _s(stoch.stoch())
        stoch_d_v = _s(stoch.stoch_signal())

    wr_v: float | None = None
    if n >= 20:
        wr_v = _s(ta.momentum.WilliamsRIndicator(high=high, low=low, close=close, lbp=14).williams_r())

    roc_v: float | None = None
    if n >= 15:
        roc_v = _s(ta.momentum.ROCIndicator(close=close, window=10).roc())

    # ── Volatility ────────────────────────────────────────────────────────────
    bb_ind = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
    bb_upper_v = _s(bb_ind.bollinger_hband())
    bb_lower_v = _s(bb_ind.bollinger_lband())
    bb_mid_v = _s(bb_ind.bollinger_mavg())
    bb_pct_b: float | None = None
    if bb_upper_v and bb_lower_v and bb_upper_v != bb_lower_v:
        bb_pct_b = (price - bb_lower_v) / (bb_upper_v - bb_lower_v)

    atr_v: float | None = None
    kc_upper_v = kc_lower_v = None
    if n >= 20:
        atr_v = _s(ta.volatility.AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range())
        kc = ta.volatility.KeltnerChannel(high=high, low=low, close=close, window=20, window_atr=10)
        kc_upper_v = _s(kc.keltner_channel_hband())
        kc_lower_v = _s(kc.keltner_channel_lband())

    # ── Volume ────────────────────────────────────────────────────────────────
    vol_sma20_v = _s(ta.trend.SMAIndicator(close=volume, window=20).sma_indicator())
    vol_ratio: float | None = None
    if vol_sma20_v and vol_sma20_v > 0:
        vol_ratio = float(volume.iloc[-1]) / vol_sma20_v

    obv_v = _s(ta.volume.OnBalanceVolumeIndicator(close=close, volume=volume).on_balance_volume())

    vwap_v: float | None = None
    if n >= 20:
        vwap_v = _s(ta.volume.VolumeWeightedAveragePrice(high=high, low=low, close=close, volume=volume).volume_weighted_average_price())

    mfi_v: float | None = None
    if n >= 20:
        mfi_v = _s(ta.volume.MFIIndicator(high=high, low=low, close=close, volume=volume, window=14).money_flow_index())

    ad_v = _s(ta.volume.AccDistIndexIndicator(high=high, low=low, close=close, volume=volume).acc_dist_index())

    # ── Statistical ───────────────────────────────────────────────────────────
    z_score_v = _rolling_zscore(close, window=20)
    rv_v = _realized_volatility(close, window=20)
    hurst_v = _hurst_exponent(close)
    autocorr_v = _autocorr_lag1(close)

    # ── Assemble snapshot ─────────────────────────────────────────────────────
    no_data = "Insufficient data"
    ich = ichimoku_vals

    return IndicatorSnapshot(
        ticker=ticker,
        timestamp=datetime.now(UTC),
        price=price,
        bar_count=n,
        # Trend
        sma_20=_iv(sma20, _interp_ma(price, sma20, "SMA20") if sma20 else no_data),
        sma_50=_iv(sma50, _interp_ma(price, sma50, "SMA50") if sma50 else no_data),
        sma_200=_iv(sma200, _interp_ma(price, sma200, "SMA200") if sma200 else no_data),
        ema_12=_iv(ema12, _interp_ma(price, ema12, "EMA12") if ema12 else no_data),
        ema_26=_iv(ema26, _interp_ma(price, ema26, "EMA26") if ema26 else no_data),
        macd_line=_iv(macd_line_v, f"MACD line: {macd_line_v:.4f}" if macd_line_v is not None else no_data),
        macd_signal=_iv(macd_sig_v, f"Signal: {macd_sig_v:.4f}" if macd_sig_v is not None else no_data),
        macd_histogram=_iv(macd_hist_v, _interp_macd(macd_hist_v, hist_prev) if macd_hist_v is not None else no_data),
        adx_14=_iv_opt(adx_v, _interp_adx(adx_v) if adx_v is not None else no_data),
        ichimoku_tenkan=_iv_opt(ich["tenkan"], _interp_ichimoku(price, ich["tenkan"] or 0, ich["kijun"] or price) if ich["tenkan"] else no_data),
        ichimoku_kijun=_iv_opt(ich["kijun"], f"Kijun (26-period baseline): {ich['kijun']:.4f}" if ich["kijun"] else no_data),
        ichimoku_senkou_a=_iv_opt(ich["senkou_a"], f"Cloud top/bottom A: {ich['senkou_a']:.4f}" if ich["senkou_a"] else no_data),
        ichimoku_senkou_b=_iv_opt(ich["senkou_b"], f"Cloud boundary B: {ich['senkou_b']:.4f}" if ich["senkou_b"] else no_data),
        # Momentum
        rsi_14=_iv(rsi_v, _interp_rsi(rsi_v) if rsi_v is not None else no_data),
        stoch_k=_iv_opt(stoch_k_v, _interp_stoch(stoch_k_v, stoch_d_v or 50) if stoch_k_v is not None else no_data),
        stoch_d=_iv_opt(stoch_d_v, f"Stochastic signal line: {stoch_d_v:.1f}" if stoch_d_v is not None else no_data),
        williams_r=_iv_opt(wr_v, _interp_williams(wr_v) if wr_v is not None else no_data),
        roc_10=_iv_opt(roc_v, f"10-period ROC: {roc_v:+.2f}%" if roc_v is not None else no_data),
        # Volatility
        bb_upper=_iv(bb_upper_v, f"Upper band: {bb_upper_v:.4f}" if bb_upper_v else no_data),
        bb_middle=_iv(bb_mid_v, f"Middle (SMA20): {bb_mid_v:.4f}" if bb_mid_v else no_data),
        bb_lower=_iv(bb_lower_v, f"Lower band: {bb_lower_v:.4f}" if bb_lower_v else no_data),
        bb_percent_b=_iv(bb_pct_b, _interp_bb(price, bb_upper_v or price, bb_lower_v or price) if bb_pct_b is not None else no_data),
        atr_14=_iv_opt(atr_v, f"ATR(14): {atr_v:.4f} — avg daily range" if atr_v is not None else no_data),
        keltner_upper=_iv_opt(kc_upper_v, f"Keltner upper: {kc_upper_v:.4f}" if kc_upper_v is not None else no_data),
        keltner_lower=_iv_opt(kc_lower_v, f"Keltner lower: {kc_lower_v:.4f}" if kc_lower_v is not None else no_data),
        # Volume
        volume_sma_20=_iv(vol_sma20_v, f"20-day avg volume: {vol_sma20_v:,.0f}" if vol_sma20_v else no_data),
        volume_ratio=_iv(vol_ratio, _interp_volume(vol_ratio) if vol_ratio is not None else no_data),
        obv=_iv_opt(obv_v, f"OBV: {obv_v:,.0f}" if obv_v is not None else no_data),
        vwap=_iv_opt(vwap_v, f"VWAP: {vwap_v:.4f}" if vwap_v is not None else no_data),
        mfi_14=_iv_opt(mfi_v, f"MFI(14): {mfi_v:.1f} {'(overbought)' if mfi_v and mfi_v > 80 else '(oversold)' if mfi_v and mfi_v < 20 else ''}" if mfi_v is not None else no_data),
        ad_line=_iv_opt(ad_v, f"A/D line: {ad_v:,.0f}" if ad_v is not None else no_data),
        # Statistical
        z_score_20=_iv_opt(z_score_v, f"Z-score (20d): {z_score_v:+.2f} {'(stretched high)' if z_score_v and z_score_v > 2 else '(stretched low)' if z_score_v and z_score_v < -2 else '(near mean)'}" if z_score_v is not None else no_data),
        realized_vol_20=_iv_opt(rv_v, f"Realized vol (20d): {rv_v:.1f}% annualised" if rv_v is not None else no_data),
        hurst_exponent=_iv_opt(hurst_v, _interp_hurst(hurst_v) if hurst_v is not None else "Need ≥50 bars"),
        autocorr_lag1=_iv_opt(autocorr_v, f"Lag-1 autocorr: {autocorr_v:+.3f} {'(momentum)' if autocorr_v and autocorr_v > 0.1 else '(mean-revert)' if autocorr_v and autocorr_v < -0.1 else '(random)'}" if autocorr_v is not None else no_data),
    )


# ── Technical score (0-100) ───────────────────────────────────────────────────

def compute_technical_score(snap: IndicatorSnapshot) -> tuple[float, str]:
    """
    Returns (score 0-100, detail string).
    Weights come from config.tech_weight_* fields.
    Formula is stable — changes belong in Phase 5 weight redesign.
    """
    from backend.app.config import get_settings
    cfg = get_settings()

    scores: dict[str, float] = {}
    details: list[str] = []

    rsi_v = snap.rsi_14.value
    if rsi_v is not None:
        # Slight penalty above 70 to reflect overbought risk
        rsi_score = 70 - (rsi_v - 70) * 0.5 if rsi_v > 70 else rsi_v
        scores["rsi"] = max(0.0, min(100.0, rsi_score))
        details.append(f"RSI: {rsi_score:.0f}/100")

    macd_h = snap.macd_histogram.value
    if macd_h is not None:
        scores["macd"] = max(0.0, min(100.0, 50 + 50 * math.tanh(macd_h * 20)))
        details.append(f"MACD: {scores['macd']:.0f}/100")

    price = snap.price
    ma_vals = [snap.sma_20.value, snap.sma_50.value, snap.sma_200.value, snap.ema_26.value]
    valid_mas = [m for m in ma_vals if m is not None]
    if valid_mas:
        above = sum(1 for m in valid_mas if price > m)
        scores["ma"] = above / len(valid_mas) * 100
        details.append(f"MA alignment: {scores['ma']:.0f}/100")

    pct_b = snap.bb_percent_b.value
    if pct_b is not None:
        scores["bb"] = max(0.0, min(100.0, pct_b * 100))
        details.append(f"BB %B: {scores['bb']:.0f}/100")

    vol_ratio = snap.volume_ratio.value
    if vol_ratio is not None:
        scores["volume"] = max(20.0, min(80.0, 40 + (vol_ratio - 1) * 20))
        details.append(f"Volume: {scores['volume']:.0f}/100")

    if not scores:
        return 50.0, "Insufficient indicator data — defaulting to neutral"

    weights = {
        "rsi": cfg.tech_weight_rsi,
        "macd": cfg.tech_weight_macd,
        "ma": cfg.tech_weight_ma,
        "bb": cfg.tech_weight_bb,
        "volume": cfg.tech_weight_volume,
    }
    total_w = sum(w for k, w in weights.items() if k in scores)
    weighted = sum(scores[k] * w for k, w in weights.items() if k in scores)
    score = (weighted / total_w) if total_w > 0 else 50.0
    return round(score, 2), " | ".join(details)


# ── Multi-timeframe analysis ──────────────────────────────────────────────────

def _timeframe_direction(snap: IndicatorSnapshot) -> float:
    """
    Compute a direction signal from -1 (bearish) to +1 (bullish)
    from a single timeframe snapshot.
    """
    signals: list[float] = []

    if snap.rsi_14.value is not None:
        signals.append((snap.rsi_14.value - 50) / 50)

    if snap.macd_histogram.value is not None:
        signals.append(math.tanh(snap.macd_histogram.value * 10))

    if snap.sma_20.value is not None and snap.sma_20.value > 0:
        pct = (snap.price - snap.sma_20.value) / snap.sma_20.value
        signals.append(math.tanh(pct * 10))

    if snap.adx_14 and snap.adx_14.value is not None:
        # ADX alone doesn't give direction — use it to scale the existing signals
        adx = snap.adx_14.value
        scale = min(1.0, adx / 40)  # 40+ ADX = full weighting
        signals = [s * scale for s in signals]

    return statistics.mean(signals) if signals else 0.0


def _agreement_label(std: float) -> str:
    if std < 0.15: return "Strong Agreement"
    if std < 0.30: return "Moderate Agreement"
    if std < 0.50: return "Weak Agreement"
    return "Diverging"


def _consensus_label(mean_dir: float) -> str:
    if mean_dir > 0.3: return "Bullish"
    if mean_dir > 0.1: return "Mildly Bullish"
    if mean_dir < -0.3: return "Bearish"
    if mean_dir < -0.1: return "Mildly Bearish"
    return "Neutral"


def compute_multi_timeframe(
    ticker: str,
    bars_by_timeframe: dict[str, list[OHLCVBar]],
) -> MultiTimeframeSnapshot:
    """
    Given OHLCV bars at multiple timeframes, compute indicators for each
    and derive a cross-timeframe agreement score.

    bars_by_timeframe keys are labels like "1h", "1d", "1w".
    Timeframes with insufficient data are silently skipped.
    """
    snapshots: dict[str, IndicatorSnapshot] = {}
    directions: dict[str, float] = {}

    for label, bars in bars_by_timeframe.items():
        snap = compute_indicators(ticker, bars)
        if snap is None:
            continue
        snapshots[label] = snap
        directions[label] = round(_timeframe_direction(snap), 3)

    if not directions:
        return MultiTimeframeSnapshot(
            ticker=ticker,
            computed_at=datetime.now(UTC),
            timeframes=snapshots,
            directions={},
            agreement_score=50.0,
            agreement_label="No data",
            consensus_direction="Unknown",
        )

    dir_values = list(directions.values())
    mean_dir = statistics.mean(dir_values)
    std_dir = statistics.stdev(dir_values) if len(dir_values) > 1 else 0.0

    # Agreement score: high when all timeframes align on same direction
    # 50 + 50 * mean_direction * (1 - std_direction)
    agreement = 1.0 - min(1.0, std_dir / 0.5)  # 0.5 std = full disagreement
    raw_score = 50.0 + 50.0 * mean_dir * agreement
    agreement_score = round(max(0.0, min(100.0, raw_score)), 1)

    return MultiTimeframeSnapshot(
        ticker=ticker,
        computed_at=datetime.now(UTC),
        timeframes=snapshots,
        directions=directions,
        agreement_score=agreement_score,
        agreement_label=_agreement_label(std_dir),
        consensus_direction=_consensus_label(mean_dir),
    )
