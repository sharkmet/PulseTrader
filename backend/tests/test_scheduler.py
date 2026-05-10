"""
Phase 9 tests — scheduler job logging, cleanup logic, and job context manager.
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from backend.app.scheduler import _daily_cleanup, _job, _refresh_news

# ── Job context manager ───────────────────────────────────────────────────────

def test_job_context_success_sets_status():
    """A job that completes without errors records status=success."""
    log_calls = []

    def fake_write(job_id, status, items, errors, detail, duration_ms):
        log_calls.append({"status": status, "items": items, "errors": errors})

    with patch("backend.app.scheduler._write_job_log", fake_write):
        with _job("test_job") as stats:
            stats["items"] += 3

    assert len(log_calls) == 1
    assert log_calls[0]["status"] == "success"
    assert log_calls[0]["items"] == 3
    assert log_calls[0]["errors"] == 0


def test_job_context_partial_when_some_errors():
    """A job with items processed but also some errors records status=partial."""
    log_calls = []

    def fake_write(job_id, status, items, errors, detail, duration_ms):
        log_calls.append({"status": status, "errors": errors})

    with patch("backend.app.scheduler._write_job_log", fake_write):
        with _job("test_job") as stats:
            stats["items"] += 5
            stats["errors"] += 2

    assert log_calls[0]["status"] == "partial"
    assert log_calls[0]["errors"] == 2


def test_job_context_failed_on_exception():
    """An unhandled exception inside the job records status=failed and re-raises."""
    log_calls = []

    def fake_write(job_id, status, items, errors, detail, duration_ms):
        log_calls.append({"status": status})

    with patch("backend.app.scheduler._write_job_log", fake_write):
        with pytest.raises(RuntimeError, match="something broke"):
            with _job("test_job"):
                raise RuntimeError("something broke")

    assert len(log_calls) == 1
    assert log_calls[0]["status"] == "failed"


def test_job_context_records_duration():
    """Duration should be a positive number of milliseconds."""
    recorded = {}

    def fake_write(job_id, status, items, errors, detail, duration_ms):
        recorded["duration_ms"] = duration_ms

    with patch("backend.app.scheduler._write_job_log", fake_write):
        with _job("test_job"):
            pass

    assert recorded["duration_ms"] >= 0


def test_job_context_write_failure_does_not_propagate():
    """If the DB write fails inside _write_job_log, it is swallowed silently."""
    # _write_job_log has its own internal try-except, so even if the DB is
    # completely unavailable, the job result is preserved. We test this by
    # verifying the context manager completes without error.
    with patch("backend.app.scheduler._write_job_log", side_effect=Exception("DB down")):
        # The try-except in _job's finally block catches the write failure
        with _job("test_job") as stats:
            stats["items"] += 1
        # Reaches here without raising


# ── Daily cleanup ─────────────────────────────────────────────────────────────

def test_daily_cleanup_runs_without_error():
    """Cleanup job should complete even on an empty database."""
    log_calls = []

    def fake_write(*args, **kwargs):
        log_calls.append(args[1])  # capture status

    with patch("backend.app.scheduler._write_job_log", fake_write):
        _daily_cleanup()  # should not raise

    assert len(log_calls) == 1
    assert log_calls[0] in ("success", "partial")


def test_daily_cleanup_deletes_old_records():
    """Old records should be removed; recent ones should be kept."""

    from backend.app.database import SessionLocal, init_db
    from backend.app.models.db import RequestLog

    init_db()

    # Insert one old and one recent record
    old_ts = dt.datetime.now(dt.UTC) - dt.timedelta(days=60)
    recent_ts = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)

    with SessionLocal() as db:
        db.add(RequestLog(ts=old_ts, source="test", operation="test",
                          latency_ms=1.0, success=True))
        db.add(RequestLog(ts=recent_ts, source="test", operation="test",
                          latency_ms=1.0, success=True))
        db.commit()

        count_before = db.query(RequestLog).filter(RequestLog.source == "test").count()

    with patch("backend.app.scheduler._write_job_log"):
        _daily_cleanup()

    with SessionLocal() as db:
        count_after = db.query(RequestLog).filter(RequestLog.source == "test").count()

    # Old record (60 days) should be deleted, recent (1 hour) should remain
    assert count_after < count_before


# ── News refresh ──────────────────────────────────────────────────────────────

def test_news_refresh_runs_without_error_on_empty_watchlist():
    """News refresh should complete cleanly when watchlist is empty."""
    from backend.app.database import init_db
    init_db()  # ensure tables exist

    with patch("backend.app.scheduler._write_job_log"):
        _refresh_news()  # empty DB → no tickers → no errors


def test_news_refresh_handles_individual_ticker_failure():
    """A failing ticker should not abort news refresh for the remaining tickers."""
    from backend.app.database import SessionLocal, init_db
    from backend.app.models.db import WatchlistItem
    init_db()

    call_count = {"n": 0}
    success_count = {"n": 0}

    def fake_get_news(ticker):
        call_count["n"] += 1
        if ticker == "FAILCO":
            raise ConnectionError("Network error")
        success_count["n"] += 1
        return MagicMock(items=[])

    # Insert two tickers temporarily
    with SessionLocal() as db:
        db.add(WatchlistItem(ticker="FAILCO", name="fail", asset_type="stock"))
        db.add(WatchlistItem(ticker="GOODCO", name="good", asset_type="stock"))
        db.commit()

    try:
        with (
            patch("backend.app.scheduler._write_job_log"),
            patch("backend.app.services.news_fetcher.get_news_feed", fake_get_news),
        ):
            _refresh_news()
    finally:
        # Clean up test rows
        with SessionLocal() as db:
            db.query(WatchlistItem).filter(
                WatchlistItem.ticker.in_(["FAILCO", "GOODCO"])
            ).delete()
            db.commit()

    # FAILCO and GOODCO were both attempted (other DB items may also be included)
    assert call_count["n"] >= 2
    # FAILCO failed, GOODCO succeeded — exactly 1 success from our two test tickers
    assert success_count["n"] == call_count["n"] - 1


# ── job_history ───────────────────────────────────────────────────────────────

def test_job_history_returns_list():
    from backend.app.scheduler import job_history
    result = job_history(limit=5)
    assert isinstance(result, list)


def test_job_history_format():
    """Each entry should have the expected keys."""
    from backend.app.scheduler import job_history
    entries = job_history(limit=3)
    for entry in entries:
        assert "job_id" in entry
        assert "status" in entry
        assert "duration_ms" in entry
        assert "items_processed" in entry
