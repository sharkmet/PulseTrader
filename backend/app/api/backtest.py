"""
Backtest API — submit jobs and retrieve results.

POST /backtest        — queue a backtest job (runs in background thread)
GET  /backtest        — list recent backtest runs
GET  /backtest/{id}   — get status + results for a specific job
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.database import SessionLocal, get_db
from backend.app.models.db import BacktestRun
from backend.app.models.schemas import BacktestRequest, BacktestResult
from backend.app.services.backtester import fetch_backtest_bars, run_backtest
from backend.app.services.score_weights import get_scoring_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/backtest", tags=["backtest"])


def _persist_result(job_id: str, result: BacktestResult) -> None:
    """Write completed/failed result back to SQLite."""
    try:
        with SessionLocal() as db:
            row = db.query(BacktestRun).filter(BacktestRun.job_id == job_id).first()
            if row:
                row.status = result.status
                row.completed_at = datetime.now(UTC)
                row.result_json = result.model_dump_json(exclude={"data_points"})
                if result.error:
                    row.error = result.error[:1000]
                db.commit()
    except Exception as exc:
        logger.error("Failed to persist backtest result for %s: %s", job_id, exc)


def _backtest_task(job_id: str, request: BacktestRequest) -> None:
    """Background thread: download data, run backtest, persist result."""
    logger.info("Starting backtest %s: %s %s → %s",
                job_id, request.ticker, request.from_date, request.to_date)
    try:
        all_bars = fetch_backtest_bars(
            ticker=request.ticker.upper(),
            from_date=request.from_date,
            to_date=request.to_date,
        )
        result = run_backtest(
            ticker=request.ticker.upper(),
            all_bars=all_bars,
            from_date=request.from_date,
            to_date=request.to_date,
            step_days=request.step_days,
            min_lookback=request.min_lookback,
        )
        result = result.model_copy(update={"job_id": job_id})
    except Exception as exc:
        logger.error("Backtest %s failed: %s", job_id, exc)
        result = BacktestResult(
            job_id=job_id,
            ticker=request.ticker.upper(),
            from_date=request.from_date,
            to_date=request.to_date,
            step_days=request.step_days,
            scoring_version=get_scoring_config().version,
            status="failed",
            created_at=datetime.now(UTC),
            error=str(exc)[:1000],
        )

    _persist_result(job_id, result)
    logger.info("Backtest %s %s (%d points)", job_id, result.status,
                len(result.data_points))


def _row_to_result(row: BacktestRun) -> BacktestResult:
    base = BacktestResult(
        job_id=row.job_id,
        ticker=row.ticker,
        from_date=row.from_date,
        to_date=row.to_date,
        step_days=row.step_days,
        scoring_version=row.scoring_version,
        status=row.status,
        created_at=row.created_at,
        completed_at=row.completed_at,
        error=row.error,
    )
    if row.result_json:
        try:
            data = json.loads(row.result_json)
            return BacktestResult(**{**base.model_dump(), **data})
        except Exception:
            pass
    return base


@router.post("", response_model=dict, status_code=202)
def start_backtest(
    request: BacktestRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """
    Queue a backtest job. Returns a `job_id` immediately.
    Poll `GET /backtest/{job_id}` to check status and retrieve results.

    The backtest downloads full price history and scores each bar in the range.
    Typical runtime: 5-30 seconds (varies with date range).
    """
    cfg = get_scoring_config()
    t = request.ticker.upper()

    # Validate date range
    try:
        from_dt = datetime.fromisoformat(request.from_date)
        to_dt = datetime.fromisoformat(request.to_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid date format: {exc}") from exc
    if from_dt >= to_dt:
        raise HTTPException(status_code=422, detail="from_date must be before to_date")

    row = BacktestRun(
        job_id="pending",  # will be assigned in background task
        ticker=t,
        from_date=request.from_date,
        to_date=request.to_date,
        step_days=request.step_days,
        scoring_version=cfg.version,
        status="running",
    )
    db.add(row)
    db.flush()  # get the row id

    # Generate job_id from DB id for uniqueness
    job_id = f"bt-{row.id:06d}"
    row.job_id = job_id
    db.commit()

    # Run in a background thread (FastAPI BackgroundTasks run after response)
    background_tasks.add_task(_backtest_task, job_id, request)

    return {
        "job_id": job_id,
        "status": "running",
        "ticker": t,
        "from_date": request.from_date,
        "to_date": request.to_date,
        "scoring_version": cfg.version,
        "message": f"Backtest queued. Poll GET /backtest/{job_id} for results.",
    }


@router.get("/{job_id}", response_model=BacktestResult)
def get_backtest(job_id: str, db: Session = Depends(get_db)) -> BacktestResult:
    """Get the status and results of a backtest job."""
    row = db.query(BacktestRun).filter(BacktestRun.job_id == job_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Backtest job '{job_id}' not found")
    return _row_to_result(row)


@router.get("", response_model=list[dict])
def list_backtests(limit: int = 20, db: Session = Depends(get_db)) -> list[dict]:
    """List recent backtest runs, newest first."""
    rows = (
        db.query(BacktestRun)
        .order_by(BacktestRun.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "job_id": r.job_id,
            "ticker": r.ticker,
            "from_date": r.from_date,
            "to_date": r.to_date,
            "step_days": r.step_days,
            "scoring_version": r.scoring_version,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in rows
    ]
