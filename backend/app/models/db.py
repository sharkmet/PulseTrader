"""SQLAlchemy ORM models."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database import Base


def _now() -> datetime:
    return datetime.now(UTC)


class WatchlistItem(Base):
    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    ticker: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="")
    asset_type: Mapped[str] = mapped_column(String(20), default="stock")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # Cached computed values (refreshed by scheduler)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_1d_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    pulse_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_refreshed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AlertRule(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)  # price_above | price_below | score_above | score_below
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    notification_channels: Mapped[str] = mapped_column(String(500), default="browser")  # comma-separated
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AiAnalysisCache(Base):
    __tablename__ = "ai_analysis_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    analysis_json: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RequestLog(Base):
    """Observability record for every external data source call."""

    __tablename__ = "request_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    source: Mapped[str] = mapped_column(String(50), index=True)
    operation: Mapped[str] = mapped_column(String(50))
    ticker: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    latency_ms: Mapped[float] = mapped_column(Float)
    success: Mapped[bool] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class SchedulerJobLog(Base):
    """One row per scheduler job execution — used by GET /admin/jobs."""

    __tablename__ = "scheduler_job_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    job_id: Mapped[str] = mapped_column(String(50), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20))   # "success" | "partial" | "failed"
    items_processed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    error_detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class BacktestRun(Base):
    """Persisted record of every backtest job — queryable for version comparison."""

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    job_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    from_date: Mapped[str] = mapped_column(String(20))
    to_date: Mapped[str] = mapped_column(String(20))
    step_days: Mapped[int] = mapped_column(Integer, default=1)
    scoring_version: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="running")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class TokenUsageLog(Base):
    """Persistent record of every Claude API call — used for budget tracking."""

    __tablename__ = "token_usage_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    purpose: Mapped[str] = mapped_column(String(100))
    ticker: Mapped[str | None] = mapped_column(String(30), nullable=True)
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float, index=True)
