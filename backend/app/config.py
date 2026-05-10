"""Central configuration — all tunable weights and env vars live here."""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class PulseWeights:
    """Immutable snapshot of scoring weights, consumed by pulse_score.py."""
    technical: float
    sentiment: float
    momentum: float
    ai: float


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── API keys ──────────────────────────────────────────────────────────────
    google_api_key: str = ""      # Gemini (free) — aistudio.google.com/app/apikey
    anthropic_api_key: str = ""   # kept for backward compat; Gemini is used when google_api_key is set
    news_api_key: str = ""
    coingecko_demo_key: str = ""
    fred_api_key: str = ""

    # ── App ───────────────────────────────────────────────────────────────────
    app_name: str = "PulseTrader"
    debug: bool = False
    cors_origins: list[str] = ["http://localhost:3000"]

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "sqlite:///./pulsetrader.db"

    # ── Cache TTLs (seconds) ──────────────────────────────────────────────────
    cache_ttl_ohlcv: int = 300        # 5 min
    cache_ttl_quote: int = 30         # 30 s
    cache_ttl_news: int = 900         # 15 min
    cache_ttl_score: int = 300        # 5 min
    cache_ttl_ai_analysis: int = 900  # 15 min

    # ── Scheduler intervals (seconds) ─────────────────────────────────────────
    scheduler_watchlist_refresh: int = 300   # 5 min
    scheduler_alert_check: int = 60          # 1 min
    scheduler_news_refresh: int = 600        # 10 min — pre-warms news cache

    # ── Sentiment engine: "vader" | "claude" ─────────────────────────────────
    sentiment_engine: str = "vader"

    # ── Pulse Score weights (must sum to 1.0) ─────────────────────────────────
    # Edit these to retune the scoring formula without touching any logic code.
    score_weight_technical: float = 0.40
    score_weight_sentiment: float = 0.30
    score_weight_momentum: float = 0.20
    score_weight_ai: float = 0.10

    # ── Technical sub-weights (must sum to 1.0) ───────────────────────────────
    tech_weight_rsi: float = 0.30
    tech_weight_macd: float = 0.25
    tech_weight_ma: float = 0.25
    tech_weight_bb: float = 0.10
    tech_weight_volume: float = 0.10

    # ── Token logging ─────────────────────────────────────────────────────────
    token_log_path: str = "logs/token_usage.jsonl"

    # ── Gemini model (used when GOOGLE_API_KEY is set) ───────────────────────
    gemini_model: str = "gemini-2.0-flash"

    # ── Claude model (fallback if ANTHROPIC_API_KEY is set instead) ──────────
    claude_model: str = "claude-haiku-4-5-20251001"

    # ── AI cost guardrails ────────────────────────────────────────────────────
    # Set to 0 to disable the budget check entirely.
    daily_token_budget_usd: float = 5.0

    # ── FinBERT (optional — pip install ".[finbert]") ─────────────────────────
    # Set SENTIMENT_ENGINE=finbert to use FinBERT instead of VADER.
    # Model is loaded lazily on first use; ~500 MB download on first run.
    finbert_model: str = "ProsusAI/finbert"
    finbert_max_items: int = 10  # max headlines to run through FinBERT per call

    @model_validator(mode="after")
    def check_weights_sum(self) -> Settings:
        pulse_total = (
            self.score_weight_technical
            + self.score_weight_sentiment
            + self.score_weight_momentum
            + self.score_weight_ai
        )
        tech_total = (
            self.tech_weight_rsi
            + self.tech_weight_macd
            + self.tech_weight_ma
            + self.tech_weight_bb
            + self.tech_weight_volume
        )
        if not math.isclose(pulse_total, 1.0, abs_tol=0.001):
            raise ValueError(f"Pulse Score weights must sum to 1.0, got {pulse_total:.4f}")
        if not math.isclose(tech_total, 1.0, abs_tol=0.001):
            raise ValueError(f"Technical sub-weights must sum to 1.0, got {tech_total:.4f}")
        return self

    @property
    def pulse_score_weights(self) -> PulseWeights:
        return PulseWeights(
            technical=self.score_weight_technical,
            sentiment=self.score_weight_sentiment,
            momentum=self.score_weight_momentum,
            ai=self.score_weight_ai,
        )

    @property
    def has_ai_key(self) -> bool:
        """True if any AI provider is configured."""
        return bool(self.google_api_key) or bool(self.anthropic_api_key)

    @property
    def has_google_key(self) -> bool:
        return bool(self.google_api_key)

    @property
    def has_anthropic_key(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def active_ai_model(self) -> str:
        """The model that will actually be called."""
        if self.google_api_key:
            return self.gemini_model
        return self.claude_model

    @property
    def has_news_api_key(self) -> bool:
        return bool(self.news_api_key)

    @property
    def has_fred_key(self) -> bool:
        return bool(self.fred_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
