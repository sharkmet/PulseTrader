"""
Pulse Score Engine v2 — 7-component decomposition.

Formula (configurable via config/score_weights.toml):
  Trend (25%)          — MA alignment, MACD, Ichimoku
  Momentum (20%)       — RSI, Stochastic, Williams %R, price returns
  Volatility (10%)     — Bollinger %B, Keltner position
  Volume (10%)         — Volume ratio, MFI
  Sentiment (15%)      — Source-weighted news sentiment
  Multi-timeframe (10%)— Cross-timeframe agreement score
  AI (10%)             — Claude qualitative overlay

Each component returns a ScoreComponent with sub_scores and data_sources for
a full reasoning trace — no black boxes.

To retune weights, edit config/score_weights.toml. Never edit math here.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime

from backend.app.config import get_settings
from backend.app.models.schemas import (
    EnrichedNewsFeed,
    IndicatorSnapshot,
    MultiTimeframeSnapshot,
    NewsFeed,
    OHLCVBar,
    PulseScoreBreakdown,
    ScoreComponent,
)
from backend.app.services.score_weights import get_scoring_config
from backend.app.services.sentiment import sentiment_to_score

# ── Label helpers ─────────────────────────────────────────────────────────────

def _score_label(score: float) -> str:
    if score >= 75: return "Strong"
    if score >= 60: return "Bullish"
    if score >= 45: return "Neutral"
    if score >= 30: return "Bearish"
    return "Weak"


def _component(
    score: float,
    weight: float,
    detail: str,
    sub_scores: dict[str, float] | None = None,
    data_sources: list[str] | None = None,
) -> ScoreComponent:
    return ScoreComponent(
        score=round(score, 2),
        weight=weight,
        weighted_contribution=round(score * weight, 2),
        detail=detail,
        sub_scores=sub_scores or {},
        data_sources=data_sources or [],
    )


def _weighted_avg(scores: dict[str, float | None], sub_weights: dict[str, float]) -> tuple[float, dict[str, float]]:
    """
    Compute weighted average of scores, skipping None values.
    Redistributes weight to available indicators.
    Returns (score 0-100, sub_scores_used).
    """
    used: dict[str, float] = {}
    total_w = 0.0
    weighted_sum = 0.0

    for key, s in scores.items():
        if s is None:
            continue
        w = sub_weights.get(key, 0.0)
        used[key] = round(s, 1)
        weighted_sum += s * w
        total_w += w

    if total_w <= 0:
        return 50.0, {}

    return round(weighted_sum / total_w, 2), used


# ── Price-return momentum (multi-timeframe returns) ───────────────────────────

def _price_return_score(bars: list[OHLCVBar]) -> tuple[float, str]:
    if len(bars) < 2:
        return 50.0, "Insufficient price history"

    latest = bars[-1].close
    details: list[str] = []

    def _ret(n: int) -> float | None:
        if len(bars) <= n:
            return None
        return (latest - bars[-n].close) / bars[-n].close

    r1, r5, r20 = _ret(2), _ret(6), _ret(21)
    available: list[tuple[float, float]] = []
    if r1 is not None:
        available.append((r1, 0.30)); details.append(f"1d: {r1*100:+.2f}%")
    if r5 is not None:
        available.append((r5, 0.35)); details.append(f"5d: {r5*100:+.2f}%")
    if r20 is not None:
        available.append((r20, 0.35)); details.append(f"20d: {r20*100:+.2f}%")

    if not available:
        return 50.0, "Insufficient data"

    total_w = sum(w for _, w in available)
    wr = sum(r * w for r, w in available) / total_w
    score = max(0.0, min(100.0, 50 + 50 * math.tanh(wr * 10)))
    return round(score, 2), " | ".join(details)


# ── 7-component scoring functions ─────────────────────────────────────────────

def _score_trend(snap: IndicatorSnapshot, cfg_sub: dict[str, float]) -> ScoreComponent:
    weight = get_scoring_config().components.trend
    sources: list[str] = []
    raw: dict[str, float | None] = {}

    # MA alignment: fraction of key MAs below price
    price = snap.price
    mas = {
        "sma_20": snap.sma_20.value,
        "sma_50": snap.sma_50.value,
        "sma_200": snap.sma_200.value,
        "ema_26": snap.ema_26.value,
    }
    valid_mas = {k: v for k, v in mas.items() if v is not None}
    if valid_mas:
        above = sum(1 for v in valid_mas.values() if price > v)
        ma_score = above / len(valid_mas) * 100
        raw["ma_alignment"] = ma_score
        sources += list(valid_mas.keys())

    # MACD histogram
    macd_h = snap.macd_histogram.value
    if macd_h is not None:
        raw["macd"] = max(0.0, min(100.0, 50 + 50 * math.tanh(macd_h * 20)))
        sources.append("macd_histogram")

    # Ichimoku
    t_val = snap.ichimoku_tenkan and snap.ichimoku_tenkan.value
    k_val = snap.ichimoku_kijun and snap.ichimoku_kijun.value
    if t_val is not None and k_val is not None:
        ich_score = 50.0
        ich_score += 25.0 if price > k_val else -25.0
        ich_score += 25.0 if t_val > k_val else -25.0
        raw["ichimoku"] = max(0.0, min(100.0, ich_score))
        sources += ["ichimoku_tenkan", "ichimoku_kijun"]

    score, sub = _weighted_avg(raw, cfg_sub)
    detail_parts = []
    if "ma_alignment" in sub:
        detail_parts.append(f"MA alignment: {sub['ma_alignment']:.0f}/100 ({sum(1 for v in valid_mas.values() if price > v)}/{len(valid_mas)} MAs below price)")
    if "macd" in sub:
        detail_parts.append(f"MACD: {sub['macd']:.0f}/100")
    if "ichimoku" in sub:
        detail_parts.append(f"Ichimoku: {sub['ichimoku']:.0f}/100")

    return _component(score, weight, " | ".join(detail_parts) or "Insufficient data", sub, sources)


def _score_momentum(snap: IndicatorSnapshot | None, bars: list[OHLCVBar], cfg_sub: dict[str, float]) -> ScoreComponent:
    weight = get_scoring_config().components.momentum
    sources: list[str] = []
    raw: dict[str, float | None] = {}

    if snap is not None:
        # RSI (with overbought penalty)
        rsi_v = snap.rsi_14.value
        if rsi_v is not None:
            rsi_score = 70 - (rsi_v - 70) * 0.5 if rsi_v > 70 else rsi_v
            raw["rsi"] = max(0.0, min(100.0, rsi_score))
            sources.append("rsi_14")

        # Stochastic K
        sk = snap.stoch_k and snap.stoch_k.value
        if sk is not None:
            raw["stochastic"] = sk
            sources.append("stoch_k")

        # Williams %R: maps -100..0 → 0..100
        wr = snap.williams_r and snap.williams_r.value
        if wr is not None:
            raw["williams_r"] = max(0.0, min(100.0, 100 + wr))
            sources.append("williams_r")

    # Price return momentum
    ret_score, ret_detail = _price_return_score(bars)
    raw["price_return"] = ret_score
    if len(bars) >= 2:
        sources.append("price_returns")

    score, sub = _weighted_avg(raw, cfg_sub)

    details = []
    if "rsi" in sub: details.append(f"RSI: {sub['rsi']:.0f}/100")
    if "stochastic" in sub: details.append(f"Stoch: {sub['stochastic']:.0f}/100")
    if "williams_r" in sub: details.append(f"W%%R: {sub['williams_r']:.0f}/100")
    if "price_return" in sub: details.append(f"Returns: {sub['price_return']:.0f}/100 ({ret_detail})")

    return _component(score, weight, " | ".join(details) or "Insufficient data", sub, sources)


def _score_volatility(snap: IndicatorSnapshot | None, cfg_sub: dict[str, float]) -> ScoreComponent:
    weight = get_scoring_config().components.volatility
    sources: list[str] = []
    raw: dict[str, float | None] = {}

    if snap is not None:
        pct_b = snap.bb_percent_b.value
        if pct_b is not None:
            raw["bb_percent_b"] = max(0.0, min(100.0, pct_b * 100))
            sources.append("bb_percent_b")

        ku = snap.keltner_upper and snap.keltner_upper.value
        kl = snap.keltner_lower and snap.keltner_lower.value
        if ku is not None and kl is not None and ku != kl:
            kc_pos = (snap.price - kl) / (ku - kl) * 100
            raw["keltner_pos"] = max(0.0, min(100.0, kc_pos))
            sources += ["keltner_upper", "keltner_lower"]

    score, sub = _weighted_avg(raw, cfg_sub)
    details = []
    if "bb_percent_b" in sub: details.append(f"BB %B: {sub['bb_percent_b']:.0f}/100")
    if "keltner_pos" in sub: details.append(f"Keltner pos: {sub['keltner_pos']:.0f}/100")

    return _component(score, weight, " | ".join(details) or "Insufficient data", sub, sources)


def _score_volume(snap: IndicatorSnapshot | None, cfg_sub: dict[str, float]) -> ScoreComponent:
    weight = get_scoring_config().components.volume
    sources: list[str] = []
    raw: dict[str, float | None] = {}

    if snap is not None:
        vr = snap.volume_ratio.value
        if vr is not None:
            # Rescale: ratio 1.0 → 50, 2.0 → 70, 0.5 → 30 (bounded 0-100)
            vr_score = max(0.0, min(100.0, 40 + (vr - 1) * 20))
            raw["volume_ratio"] = vr_score
            sources.append("volume_ratio")

        mfi_v = snap.mfi_14 and snap.mfi_14.value
        if mfi_v is not None:
            raw["mfi"] = mfi_v  # MFI is already 0-100
            sources.append("mfi_14")
        elif vr is not None:
            # MFI unavailable — use volume_ratio for its slot too
            raw["mfi"] = raw.get("volume_ratio", 50.0)

    score, sub = _weighted_avg(raw, cfg_sub)
    details = []
    if "volume_ratio" in sub: details.append(f"Vol ratio: {sub['volume_ratio']:.0f}/100")
    if "mfi" in sub: details.append(f"MFI: {sub['mfi']:.0f}/100")

    return _component(score, weight, " | ".join(details) or "Insufficient data", sub, sources)


def _score_sentiment(news_feed: NewsFeed | None, weight: float) -> ScoreComponent:
    if news_feed is None or not news_feed.items:
        return _component(50.0, weight, "No news data — using neutral", {}, [])

    # Use source-weighted compound if available (EnrichedNewsFeed), else rolling_sentiment
    if isinstance(news_feed, EnrichedNewsFeed) and news_feed.source_weighted_sentiment != 0.0:
        compound = news_feed.source_weighted_sentiment
        kind = "source-weighted"
    else:
        compound = news_feed.rolling_sentiment
        kind = "simple mean"

    score = sentiment_to_score(compound)
    momentum_hint = ""
    if isinstance(news_feed, EnrichedNewsFeed) and news_feed.momentum:
        m = news_feed.momentum
        if m.direction != "stable":
            momentum_hint = f" (momentum {m.direction}: {m.delta:+.3f})"

    detail = (
        f"{kind} sentiment {compound:+.3f} ({news_feed.sentiment_label}) "
        f"from {len(news_feed.items)} headlines{momentum_hint}"
    )
    return _component(score, weight, detail, {"sentiment": round(score, 1)}, ["news_feed"])


def _score_multi_timeframe(mtf: MultiTimeframeSnapshot | None, weight: float) -> ScoreComponent:
    if mtf is None:
        return _component(
            50.0, weight,
            "Multi-timeframe not computed — using neutral (add ?include_mtf=true)",
            {}, [],
        )
    timeframes_str = ", ".join(f"{tf}:{d:+.2f}" for tf, d in mtf.directions.items())
    detail = (
        f"{mtf.agreement_label}: {mtf.consensus_direction} "
        f"({timeframes_str}) — agreement {mtf.agreement_score:.0f}/100"
    )
    return _component(
        mtf.agreement_score, weight, detail,
        {tf: round(d, 3) for tf, d in mtf.directions.items()},
        list(mtf.timeframes.keys()),
    )


def _score_ai(ai_score: float, weight: float, has_key: bool) -> ScoreComponent:
    # Always use the passed ai_score (caller passes 50.0 when no key is set).
    # Only the detail message changes based on whether a key is configured.
    detail = (
        f"Claude qualitative read: {ai_score:.0f}/100"
        if has_key
        else f"AI disabled (no ANTHROPIC_API_KEY) — using neutral {ai_score:.0f}"
    )
    return _component(
        ai_score, weight, detail,
        {"ai_score": round(ai_score, 1)},
        ["anthropic_claude"] if has_key else [],
    )


# ── Main entry point ──────────────────────────────────────────────────────────

def compute_pulse_score(
    ticker: str,
    bars: list[OHLCVBar],
    snap: IndicatorSnapshot | None,
    news_feed: NewsFeed | None,
    ai_score: float = 50.0,
    mtf_snapshot: MultiTimeframeSnapshot | None = None,
) -> PulseScoreBreakdown:
    """
    Compute the full 7-component Pulse Score.
    Weights loaded from config/score_weights.toml.
    Every component includes sub_scores and data_sources for full transparency.
    """
    cfg = get_scoring_config()
    w = cfg.components
    settings = get_settings()

    trend = _score_trend(snap, cfg.sub("trend")) if snap else _component(50.0, w.trend, "No indicator data", {}, [])
    momentum = _score_momentum(snap, bars, cfg.sub("momentum"))
    volatility = _score_volatility(snap, cfg.sub("volatility"))
    volume = _score_volume(snap, cfg.sub("volume"))
    sentiment = _score_sentiment(news_feed, w.sentiment)
    mtf = _score_multi_timeframe(mtf_snapshot, w.multi_timeframe)
    ai_comp = _score_ai(ai_score, w.ai, settings.has_anthropic_key)

    overall = round(max(0.0, min(100.0,
        trend.weighted_contribution
        + momentum.weighted_contribution
        + volatility.weighted_contribution
        + volume.weighted_contribution
        + sentiment.weighted_contribution
        + mtf.weighted_contribution
        + ai_comp.weighted_contribution
    )), 2)

    return PulseScoreBreakdown(
        ticker=ticker,
        overall=overall,
        label=_score_label(overall),
        scoring_version=cfg.version,
        trend=trend,
        momentum=momentum,
        volatility=volatility,
        volume=volume,
        sentiment=sentiment,
        multi_timeframe=mtf,
        ai=ai_comp,
        computed_at=datetime.now(UTC),
    )
