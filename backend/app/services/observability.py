"""
Request observability — structured logging for every external data source call.

Each call is logged via loguru (always) and written to the `request_log` SQLite
table (best-effort; DB errors never surface to the caller).

Usage:
    with observe("yfinance", "ohlcv", ticker="AAPL"):
        bars = yf.Ticker("AAPL").history(...)
"""
from __future__ import annotations

import contextlib
import time
from collections.abc import Generator

from loguru import logger


@contextlib.contextmanager
def observe(source: str, operation: str, ticker: str = "") -> Generator[None, None, None]:
    """Context manager that measures latency and records success/failure."""
    start = time.monotonic()
    error_msg: str | None = None
    try:
        yield
    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        success = error_msg is None
        logger.bind(
            source=source,
            operation=operation,
            ticker=ticker,
            latency_ms=latency_ms,
            success=success,
        ).debug("external_request")

        _persist(
            source=source,
            operation=operation,
            ticker=ticker or None,
            latency_ms=latency_ms,
            success=success,
            error=error_msg,
        )


def _persist(
    source: str,
    operation: str,
    ticker: str | None,
    latency_ms: float,
    success: bool,
    error: str | None,
) -> None:
    """Write to SQLite request_log. Silently swallow errors — never break a data call."""
    try:
        from backend.app.database import SessionLocal
        from backend.app.models.db import RequestLog

        with SessionLocal() as db:
            db.add(
                RequestLog(
                    source=source,
                    operation=operation,
                    ticker=ticker,
                    latency_ms=latency_ms,
                    success=success,
                    error=error[:500] if error else None,
                )
            )
            db.commit()
    except Exception as exc:
        logger.debug("observability DB write failed: {}", exc)


def recent_requests(limit: int = 100) -> list[dict]:
    """Return recent request log entries, newest first."""
    try:
        from backend.app.database import SessionLocal
        from backend.app.models.db import RequestLog

        with SessionLocal() as db:
            rows = (
                db.query(RequestLog)
                .order_by(RequestLog.ts.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "ts": row.ts.isoformat(),
                    "source": row.source,
                    "operation": row.operation,
                    "ticker": row.ticker,
                    "latency_ms": row.latency_ms,
                    "success": row.success,
                    "error": row.error,
                }
                for row in rows
            ]
    except Exception:
        return []


def source_stats() -> dict[str, dict]:
    """Aggregate success rate and median latency per source (last 1000 calls)."""
    try:
        import statistics

        from backend.app.database import SessionLocal
        from backend.app.models.db import RequestLog

        with SessionLocal() as db:
            rows = (
                db.query(RequestLog)
                .order_by(RequestLog.ts.desc())
                .limit(1000)
                .all()
            )

        by_source: dict[str, list] = {}
        for row in rows:
            by_source.setdefault(row.source, []).append(row)

        result = {}
        for src, entries in by_source.items():
            total = len(entries)
            successes = sum(1 for e in entries if e.success)
            latencies = [e.latency_ms for e in entries]
            result[src] = {
                "total_calls": total,
                "success_rate": round(successes / total, 3) if total else 0,
                "median_latency_ms": round(statistics.median(latencies), 1) if latencies else 0,
                "p95_latency_ms": round(sorted(latencies)[int(len(latencies) * 0.95)], 1) if latencies else 0,
            }
        return result
    except Exception:
        return {}
