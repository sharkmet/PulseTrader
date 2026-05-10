"""Pydantic schemas — the contract between backend and frontend."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# ── OHLCV / Price ─────────────────────────────────────────────────────────────

class OHLCVBar(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class Quote(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "ticker": "AAPL", "name": "Apple Inc.", "price": 189.37,
        "change_1d": 2.45, "change_1d_pct": 1.31, "volume": 55234100,
        "market_cap": 2945000000000, "asset_type": "stock", "currency": "USD",
        "last_updated": "2024-11-15T21:00:00Z",
    }})
    ticker: str
    name: str
    price: float
    change_1d: float        # absolute
    change_1d_pct: float    # percent
    volume: float
    market_cap: float | None = None
    asset_type: str = "stock"  # "stock" | "crypto"
    currency: str = "USD"
    last_updated: datetime


# ── Indicators ────────────────────────────────────────────────────────────────

class IndicatorValue(BaseModel):
    value: float | None
    interpretation: str


class IndicatorSnapshot(BaseModel):
    ticker: str
    timestamp: datetime
    price: float
    bar_count: int = 0          # number of bars used to compute

    # ── Trend ─────────────────────────────────────────────────────────────────
    sma_20: IndicatorValue
    sma_50: IndicatorValue
    sma_200: IndicatorValue
    ema_12: IndicatorValue
    ema_26: IndicatorValue
    macd_line: IndicatorValue
    macd_signal: IndicatorValue
    macd_histogram: IndicatorValue
    adx_14: IndicatorValue | None = None        # Average Directional Index (trend strength)
    ichimoku_tenkan: IndicatorValue | None = None   # 9-period midpoint
    ichimoku_kijun: IndicatorValue | None = None    # 26-period midpoint
    ichimoku_senkou_a: IndicatorValue | None = None # cloud top/bottom (current)
    ichimoku_senkou_b: IndicatorValue | None = None

    # ── Momentum ──────────────────────────────────────────────────────────────
    rsi_14: IndicatorValue
    stoch_k: IndicatorValue | None = None       # Stochastic %K
    stoch_d: IndicatorValue | None = None       # Stochastic %D (signal)
    williams_r: IndicatorValue | None = None    # Williams %R (-100 to 0)
    roc_10: IndicatorValue | None = None        # Rate of Change (10-period)

    # ── Volatility ────────────────────────────────────────────────────────────
    bb_upper: IndicatorValue
    bb_middle: IndicatorValue
    bb_lower: IndicatorValue
    bb_percent_b: IndicatorValue
    atr_14: IndicatorValue | None = None        # Average True Range
    keltner_upper: IndicatorValue | None = None
    keltner_lower: IndicatorValue | None = None

    # ── Volume ────────────────────────────────────────────────────────────────
    volume_sma_20: IndicatorValue
    volume_ratio: IndicatorValue                # current / sma_20
    obv: IndicatorValue | None = None           # On Balance Volume
    vwap: IndicatorValue | None = None          # Volume Weighted Avg Price
    mfi_14: IndicatorValue | None = None        # Money Flow Index
    ad_line: IndicatorValue | None = None       # Accumulation/Distribution

    # ── Statistical ───────────────────────────────────────────────────────────
    z_score_20: IndicatorValue | None = None    # price z-score vs 20-period mean
    realized_vol_20: IndicatorValue | None = None  # annualised 20-day realized vol (%)
    hurst_exponent: IndicatorValue | None = None   # H: >0.5 trending, <0.5 mean-reverting
    autocorr_lag1: IndicatorValue | None = None    # lag-1 autocorrelation of log-returns


# ── Multi-Timeframe ───────────────────────────────────────────────────────────

class MultiTimeframeSnapshot(BaseModel):
    ticker: str
    computed_at: datetime
    timeframes: dict[str, IndicatorSnapshot]   # e.g. "1h", "1d", "1w"
    directions: dict[str, float]               # timeframe → direction score -1 to +1
    agreement_score: float                     # 0-100
    agreement_label: str                       # "Strong Agreement" | "Moderate" | "Diverging"
    consensus_direction: str                   # "Bullish" | "Bearish" | "Neutral" | "Mixed"


# ── News & Sentiment ──────────────────────────────────────────────────────────

class NewsItem(BaseModel):
    title: str
    url: str
    publisher: str
    published_at: datetime
    summary: str = ""
    sentiment_score: float | None = None   # -1 to +1, compound score
    sentiment_label: str = ""              # "bullish" | "bearish" | "neutral"
    sentiment_engine: str = ""             # "vader" | "finbert" | "claude"
    source_weight: float = 1.0             # credibility multiplier
    entities: list[str] = []              # extracted ticker/company mentions


class NewsFeed(BaseModel):
    ticker: str
    items: list[NewsItem]
    rolling_sentiment: float               # -1 to +1
    sentiment_label: str


# ── Enriched sentiment (Phase 4) ─────────────────────────────────────────────

class SentimentWindow(BaseModel):
    window: str                   # "1h", "24h", "7d"
    item_count: int
    compound: float               # simple mean, -1 to +1
    weighted_compound: float      # source-credibility weighted, -1 to +1
    label: str                    # bullish/bearish/neutral


class SentimentMomentum(BaseModel):
    direction: str                # "rising" | "falling" | "stable"
    delta: float                  # recent compound minus baseline compound
    detail: str                   # plain-English explanation


class EnrichedNewsFeed(NewsFeed):
    """NewsFeed extended with temporal windows, momentum, and entity data."""
    windows: dict[str, SentimentWindow] = {}
    momentum: SentimentMomentum | None = None
    source_weighted_sentiment: float = 0.0  # overall source-weighted compound
    top_entities: list[str] = []            # most-mentioned tickers/companies
    engines_used: list[str] = []            # which sentiment engines ran


# ── Pulse Score ───────────────────────────────────────────────────────────────

class ScoreComponent(BaseModel):
    score: float                           # 0-100
    weight: float                          # contribution weight
    weighted_contribution: float           # score * weight
    detail: str                            # plain-English explanation
    sub_scores: dict[str, float] = {}     # individual indicator contributions
    data_sources: list[str] = []          # indicator names used


class PulseScoreBreakdown(BaseModel):
    ticker: str
    overall: float = Field(..., ge=0, le=100)
    label: str                             # "Strong" | "Bullish" | "Neutral" | etc.
    scoring_version: str = "2.0.0"        # formula version — for backtest comparison
    # ── 7 components ─────────────────────────────────────────────────────────
    trend: ScoreComponent                  # MA alignment, MACD, Ichimoku (25%)
    momentum: ScoreComponent               # RSI, Stochastic, Williams %R, returns (20%)
    volatility: ScoreComponent             # Bollinger %B, Keltner position (10%)
    volume: ScoreComponent                 # Volume ratio, MFI (10%)
    sentiment: ScoreComponent              # Source-weighted news sentiment (15%)
    multi_timeframe: ScoreComponent        # Cross-timeframe agreement (10%)
    ai: ScoreComponent                     # Claude qualitative overlay (10%)
    computed_at: datetime
    data_freshness_seconds: int | None = None


# ── AI Analysis ───────────────────────────────────────────────────────────────

class AiPriceTarget(BaseModel):
    low: float
    mid: float
    high: float
    horizon: str = "12M"


class AiAnalysis(BaseModel):
    ticker: str
    summary: str
    bullish_factors: list[str]
    bearish_factors: list[str]
    watch_for: list[str]
    ai_score: float                        # 0-100 qualitative strength read
    price_target: AiPriceTarget | None = None
    model_used: str
    cached: bool = False
    generated_at: datetime
    input_tokens: int | None = None
    output_tokens: int | None = None


# ── Watchlist ─────────────────────────────────────────────────────────────────

class WatchlistItemCreate(BaseModel):
    ticker: str
    name: str = ""
    asset_type: str = "stock"


class WatchlistItemOut(BaseModel):
    id: int
    ticker: str
    name: str
    asset_type: str
    added_at: datetime
    price: float | None = None
    change_1d_pct: float | None = None
    pulse_score: float | None = None


# ── Alerts ────────────────────────────────────────────────────────────────────

class AlertCreate(BaseModel):
    ticker: str
    alert_type: str
    threshold: float
    notification_channels: list[str] = Field(default_factory=lambda: ["browser"])


class AlertOut(BaseModel):
    id: int
    ticker: str
    alert_type: str
    threshold: float
    notification_channels: list[str]
    is_active: bool
    triggered_at: datetime | None = None
    created_at: datetime


class TriggeredAlert(BaseModel):
    alert: AlertOut
    current_value: float
    message: str
    triggered_at: datetime


# ── Market Brief (AI-generated overview) ─────────────────────────────────────

class MarketBriefBullet(BaseModel):
    tag: str    # "BULL" | "BEAR" | "WATCH"
    text: str


class MarketBrief(BaseModel):
    headline: str
    body: str
    posture: str
    confidence: int
    bullets: list[MarketBriefBullet]
    generated_at: datetime
    cached: bool = False
    model_used: str


# ── News Interpretation (Phase 6 AI) ─────────────────────────────────────────

class NewsTheme(BaseModel):
    theme: str
    sentiment: str          # "bullish" | "bearish" | "neutral"
    headline_count: int


class NewsInterpretation(BaseModel):
    ticker: str
    summary: str
    themes: list[NewsTheme]
    overall_sentiment: str
    key_catalysts: list[str]
    risk_factors: list[str]
    model_used: str
    cached: bool = False
    generated_at: datetime


# ── Usage / Budget reporting ──────────────────────────────────────────────────

class PeriodUsage(BaseModel):
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class UsageReport(BaseModel):
    today: PeriodUsage
    this_month: PeriodUsage
    daily_budget_usd: float
    budget_remaining_usd: float
    budget_utilization_pct: float
    budget_status: str          # "ok" | "warning" | "exceeded"
    recent_calls: list[dict]


# ── Backtesting (Phase 8) ─────────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    ticker: str
    from_date: str          # YYYY-MM-DD
    to_date: str            # YYYY-MM-DD
    step_days: int = Field(default=1, ge=1, le=30, description="Compute score every N trading days")
    min_lookback: int = Field(default=60, ge=30, description="Minimum bars required to compute indicators")


class BacktestDataPoint(BaseModel):
    date: datetime
    score: float
    price: float
    trend_score: float | None = None
    momentum_score: float | None = None
    volatility_score: float | None = None
    return_1d: float | None = None   # forward return: t → t+1
    return_7d: float | None = None   # forward return: t → t+7
    return_30d: float | None = None  # forward return: t → t+30


class CalibrationBucket(BaseModel):
    bucket: str             # e.g. "60-80"
    n: int
    mean_return_1d: float | None = None
    mean_return_7d: float | None = None
    mean_return_30d: float | None = None


class BacktestMetrics(BaseModel):
    n_predictions: int
    n_with_1d_data: int
    n_with_7d_data: int
    n_with_30d_data: int
    mean_score: float
    score_std: float
    # Directional accuracy: sign(score - 50) == sign(forward_return)
    accuracy_1d: float | None = None
    accuracy_7d: float | None = None
    accuracy_30d: float | None = None
    # Sharpe of naive long/short strategy (long if score > 50, else short)
    sharpe_1d: float | None = None
    max_drawdown_pct: float | None = None
    # Calibration: score buckets vs realised returns
    calibration: list[CalibrationBucket] = []


class BacktestResult(BaseModel):
    job_id: str
    ticker: str
    from_date: str
    to_date: str
    step_days: int
    scoring_version: str
    status: str             # "running" | "completed" | "failed"
    created_at: datetime
    completed_at: datetime | None = None
    metrics: BacktestMetrics | None = None
    data_points: list[BacktestDataPoint] = []
    report_markdown: str | None = None
    error: str | None = None


# ── Full asset snapshot (Phase 7) ─────────────────────────────────────────────

class AssetSnapshot(BaseModel):
    """
    One-request aggregate of all analysis for a ticker.
    Fields are None when data is unavailable or excluded by query params.
    """
    ticker: str
    asset_type: str = "stock"
    fetched_at: datetime
    quote: Quote | None = None
    indicators: IndicatorSnapshot | None = None
    pulse_score: PulseScoreBreakdown | None = None
    news: EnrichedNewsFeed | None = None
    multi_timeframe: MultiTimeframeSnapshot | None = None
    macro_context: str | None = None   # one-line FRED summary or None
    data_sources_used: list[str] = []  # which sources provided data
    errors: list[str] = []             # non-fatal pipeline errors


# ── Generic responses ─────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    version: str = "1.0.0"
    message: str = "PulseTrader is running. Educational use only — not financial advice."


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
