"""
Background scheduler — four recurring jobs:

  watchlist_refresh  (5 min)  — update price + pulse score for all watchlist items
  alert_check        (1 min)  — evaluate alert rules against cached values
  news_refresh       (10 min) — pre-warm news cache for watchlist tickers
  daily_cleanup      (daily)  — prune old DB records to prevent unbounded growth

Every job run is logged to `scheduler_job_log` with duration, item count, and
error count. View recent runs at GET /admin/jobs.
"""
from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler

from backend.app.config import get_settings

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


# ── Job execution context ─────────────────────────────────────────────────────

@contextlib.contextmanager
def _job(job_id: str) -> Generator[dict[str, Any], None, None]:
    """
    Context manager that:
    - Logs job start/finish/duration to loguru
    - Writes a SchedulerJobLog row on completion
    - Tracks items_processed and errors via a mutable stats dict

    Usage:
        with _job("watchlist_refresh") as stats:
            for item in items:
                stats["items"] += 1
                ...
    """
    start = time.monotonic()
    stats: dict[str, Any] = {"items": 0, "errors": 0, "detail": None}
    status = "failed"

    logger.debug("Job starting: %s", job_id)
    try:
        yield stats
        status = "partial" if stats["errors"] > 0 else "success"
    except Exception as exc:
        stats["errors"] += 1
        stats["detail"] = str(exc)[:1000]
        status = "failed"
        logger.error("Job %s failed: %s", job_id, exc)
        raise  # propagate so callers know the job failed
    finally:
        duration_ms = round((time.monotonic() - start) * 1000, 1)
        logger.info(
            "Job %s %s: %d items, %d errors, %.0fms",
            job_id, status, stats["items"], stats["errors"], duration_ms,
        )
        try:
            _write_job_log(job_id, status, stats["items"], stats["errors"], stats["detail"], duration_ms)
        except Exception as log_exc:
            logger.debug("Job log write failed for %s: %s", job_id, log_exc)


def _write_job_log(
    job_id: str, status: str, items: int, errors: int, detail: str | None, duration_ms: float,
) -> None:
    try:
        from backend.app.database import SessionLocal
        from backend.app.models.db import SchedulerJobLog
        with SessionLocal() as db:
            db.add(SchedulerJobLog(
                job_id=job_id,
                completed_at=datetime.now(UTC),
                duration_ms=duration_ms,
                status=status,
                items_processed=items,
                errors=errors,
                error_detail=detail,
            ))
            db.commit()
    except Exception as exc:
        logger.debug("Job log write failed: %s", exc)


# ── Job implementations ───────────────────────────────────────────────────────

def _refresh_watchlist() -> None:
    """Update price and pulse score for every watchlist item."""
    from backend.app.database import SessionLocal
    from backend.app.models.db import WatchlistItem
    from backend.app.services.data_fetcher import get_ohlcv, get_quote
    from backend.app.services.indicators import compute_indicators
    from backend.app.services.news_fetcher import get_news_feed
    from backend.app.services.pulse_score import compute_pulse_score

    with _job("watchlist_refresh") as stats:
        with SessionLocal() as db:
            items = db.query(WatchlistItem).all()

        for item in items:
            try:
                quote = get_quote(item.ticker)
                bars = get_ohlcv(item.ticker, period="6mo", interval="1d")

                if quote or bars:
                    with SessionLocal() as db:
                        row = db.query(WatchlistItem).filter(WatchlistItem.ticker == item.ticker).first()
                        if row:
                            if quote:
                                row.price = quote.price
                                row.change_1d_pct = quote.change_1d_pct
                            if bars:
                                snap = compute_indicators(item.ticker, bars)
                                news = get_news_feed(item.ticker)
                                score = compute_pulse_score(
                                    ticker=item.ticker,
                                    bars=bars,
                                    snap=snap,
                                    news_feed=news,
                                    ai_score=50.0,  # no AI in background
                                )
                                row.pulse_score = score.overall
                            row.last_refreshed = datetime.now(UTC)
                            db.commit()

                stats["items"] += 1
                logger.debug("Refreshed %s: price=%s score=%s",
                             item.ticker, quote and quote.price, item.pulse_score)
            except Exception as exc:
                stats["errors"] += 1
                logger.warning("Watchlist refresh failed for %s: %s", item.ticker, exc)


def _check_alerts() -> None:
    """Evaluate all active alert rules against latest cached values."""
    from backend.app.database import SessionLocal
    from backend.app.models.db import AlertRule, WatchlistItem

    with _job("alert_check") as stats:
        with SessionLocal() as db:
            active = db.query(AlertRule).filter(AlertRule.is_active == True).all()  # noqa: E712

        for alert in active:
            try:
                with SessionLocal() as db:
                    item = db.query(WatchlistItem).filter(
                        WatchlistItem.ticker == alert.ticker
                    ).first()

                    if not item:
                        continue

                    current: float | None = None
                    if alert.alert_type in ("price_above", "price_below"):
                        current = item.price
                    elif alert.alert_type in ("score_above", "score_below"):
                        current = item.pulse_score

                    if current is None:
                        continue

                    triggered = (
                        (alert.alert_type.endswith("_above") and current >= alert.threshold)
                        or (alert.alert_type.endswith("_below") and current <= alert.threshold)
                    )

                    if triggered and not alert.triggered_at:
                        alert.triggered_at = datetime.now(UTC)
                        alert.is_active = False
                        db.add(alert)
                        db.commit()
                        logger.info(
                            "Alert triggered: %s %s %.4f (current=%.4f)",
                            alert.ticker, alert.alert_type, alert.threshold, current,
                        )

                stats["items"] += 1
            except Exception as exc:
                stats["errors"] += 1
                logger.warning("Alert check failed for id=%s: %s", alert.id, exc)


def _refresh_news() -> None:
    """
    Pre-warm the news cache for every watchlist ticker.
    Runs at a lower frequency than watchlist_refresh to avoid rate-limiting.
    This ensures news is already scored and cached when users request it.
    """
    from backend.app.database import SessionLocal
    from backend.app.models.db import WatchlistItem
    from backend.app.services.news_fetcher import get_news_feed

    with _job("news_refresh") as stats:
        with SessionLocal() as db:
            tickers = [item.ticker for item in db.query(WatchlistItem).all()]

        for ticker in tickers:
            try:
                get_news_feed(ticker)  # populates in-memory cache
                stats["items"] += 1
            except Exception as exc:
                stats["errors"] += 1
                logger.warning("News refresh failed for %s: %s", ticker, exc)


def _daily_cleanup() -> None:
    """
    Prune old DB records to prevent unbounded growth.
    Runs once daily at midnight UTC (configured via APScheduler cron).

    Retention policy:
      request_log       — 30 days
      token_usage_log   — 90 days
      scheduler_job_log — 30 days
      ai_analysis_cache — 2 days (TTL-checked on every request too)
      backtest_runs     — 180 days
    """
    from backend.app.database import SessionLocal
    from backend.app.models.db import (
        AiAnalysisCache,
        BacktestRun,
        RequestLog,
        SchedulerJobLog,
        TokenUsageLog,
    )

    retention: list[tuple[Any, str, int]] = [
        (RequestLog,      "ts",           30),
        (TokenUsageLog,   "ts",           90),
        (SchedulerJobLog, "started_at",   30),
        (AiAnalysisCache, "generated_at",  2),
        (BacktestRun,     "created_at",  180),
    ]

    with _job("daily_cleanup") as stats:
        for Model, ts_field, days in retention:
            try:
                cutoff = datetime.now(UTC) - timedelta(days=days)
                col = getattr(Model, ts_field)
                with SessionLocal() as db:
                    deleted = db.query(Model).filter(col < cutoff).delete()
                    db.commit()
                if deleted:
                    logger.info("Cleanup %s: deleted %d rows older than %dd",
                                Model.__tablename__, deleted, days)
                stats["items"] += deleted
            except Exception as exc:
                stats["errors"] += 1
                logger.warning("Cleanup failed for %s: %s", Model.__tablename__, exc)


# ── Scheduler lifecycle ───────────────────────────────────────────────────────

def start_scheduler() -> None:
    global _scheduler
    settings = get_settings()
    _scheduler = BackgroundScheduler(timezone="UTC")

    _scheduler.add_job(
        _refresh_watchlist, "interval",
        seconds=settings.scheduler_watchlist_refresh,
        id="watchlist_refresh", max_instances=1,
    )
    _scheduler.add_job(
        _check_alerts, "interval",
        seconds=settings.scheduler_alert_check,
        id="alert_check", max_instances=1,
    )
    _scheduler.add_job(
        _refresh_news, "interval",
        seconds=settings.scheduler_news_refresh,
        id="news_refresh", max_instances=1,
    )
    _scheduler.add_job(
        _daily_cleanup, "cron",
        hour=2, minute=0,       # 02:00 UTC daily
        id="daily_cleanup", max_instances=1,
    )

    _scheduler.start()
    logger.info(
        "Scheduler started — watchlist=%ds alerts=%ds news=%ds cleanup=daily@02:00UTC",
        settings.scheduler_watchlist_refresh,
        settings.scheduler_alert_check,
        settings.scheduler_news_refresh,
    )


def stop_scheduler() -> None:
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


def job_history(limit: int = 50) -> list[dict]:
    """Return recent job run records for the admin dashboard."""
    try:
        from backend.app.database import SessionLocal
        from backend.app.models.db import SchedulerJobLog
        with SessionLocal() as db:
            rows = (
                db.query(SchedulerJobLog)
                .order_by(SchedulerJobLog.started_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "job_id": r.job_id,
                    "started_at": r.started_at.isoformat(),
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                    "duration_ms": r.duration_ms,
                    "status": r.status,
                    "items_processed": r.items_processed,
                    "errors": r.errors,
                    "error_detail": r.error_detail,
                }
                for r in rows
            ]
    except Exception:
        return []
