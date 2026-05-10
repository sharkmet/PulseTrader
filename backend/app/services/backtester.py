"""
Backtesting engine — replay the Pulse Score through historical data.

LOOKAHEAD POLICY (enforced by assertion):
  When computing the score at date T, only bars with timestamp <= T may be
  used. `_bars_up_to()` filters and asserts this invariant. Any regression
  that passes future data to the scoring engine will fail loudly.

  The EVALUATION step (computing forward returns) legitimately uses future
  prices — that's the whole point — but the SCORE is already fixed and
  immutable by then.

Usage:
  bars = fetch_backtest_bars("AAPL", "2022-01-01", "2024-12-31")
  result = run_backtest("AAPL", bars, from_dt, to_dt)
"""
from __future__ import annotations

import logging
import math
import statistics
import uuid
from datetime import UTC, datetime, timedelta

from backend.app.models.schemas import (
    BacktestDataPoint,
    BacktestMetrics,
    BacktestResult,
    CalibrationBucket,
    OHLCVBar,
)
from backend.app.services.indicators import compute_indicators
from backend.app.services.pulse_score import compute_pulse_score
from backend.app.services.score_weights import get_scoring_config

logger = logging.getLogger(__name__)

_SCORE_BUCKETS = [
    ("0-20",  0,  20),
    ("20-40", 20, 40),
    ("40-60", 40, 60),
    ("60-80", 60, 80),
    ("80-100",80,100),
]


# ── No-lookahead filter ───────────────────────────────────────────────────────

def _bars_up_to(bars: list[OHLCVBar], cutoff: datetime) -> list[OHLCVBar]:
    """
    Return bars with timestamp <= cutoff.

    Raises AssertionError if any bar in the result exceeds the cutoff —
    this is the primary lookahead-bias guard.
    """
    # Cutoff must be timezone-aware to compare with bar timestamps
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=UTC)

    result = [b for b in bars if b.timestamp <= cutoff]

    # Explicit assertion — never silently pass future data through
    for b in result:
        bar_ts = b.timestamp if b.timestamp.tzinfo else b.timestamp.replace(tzinfo=UTC)
        assert bar_ts <= cutoff, (
            f"LOOKAHEAD BIAS DETECTED: bar timestamp {bar_ts} > cutoff {cutoff}"
        )
    return result


# ── Data download ─────────────────────────────────────────────────────────────

def fetch_backtest_bars(
    ticker: str,
    from_date: str,
    to_date: str,
    extra_lookback_days: int = 365,
    extra_forward_days: int = 45,
) -> list[OHLCVBar]:
    """
    Download the full OHLCV history needed for a backtest.

    Downloads `extra_lookback_days` before `from_date` so indicators can be
    computed at the very start of the backtest period. Downloads
    `extra_forward_days` after `to_date` to measure forward returns for
    the last scoring points.
    """
    from_dt = datetime.fromisoformat(from_date)
    to_dt = datetime.fromisoformat(to_date)
    dl_start = (from_dt - timedelta(days=extra_lookback_days)).strftime("%Y-%m-%d")
    dl_end = (to_dt + timedelta(days=extra_forward_days)).strftime("%Y-%m-%d")

    logger.info("Downloading %s bars: %s → %s (backtest: %s → %s)",
                ticker, dl_start, dl_end, from_date, to_date)

    try:
        import yfinance as yf
        yticker = yf.Ticker(ticker.upper())
        hist = yticker.history(start=dl_start, end=dl_end, interval="1d", auto_adjust=True)
    except Exception as exc:
        logger.error("Data download failed for %s: %s", ticker, exc)
        return []

    if hist.empty:
        return []

    bars: list[OHLCVBar] = []
    for ts, row in hist.iterrows():
        try:
            bars.append(OHLCVBar(
                timestamp=ts.to_pydatetime().replace(tzinfo=UTC),
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row["Volume"]),
            ))
        except (ValueError, KeyError):
            continue

    return sorted(bars, key=lambda b: b.timestamp)


# ── Forward return attachment ─────────────────────────────────────────────────

def _attach_forward_returns(
    data_points: list[BacktestDataPoint],
    all_bars: list[OHLCVBar],
) -> None:
    """
    Compute and attach forward returns to each data point.
    Uses future prices from `all_bars` — this is MEASUREMENT, not prediction.
    The score was already fixed at its respective date; looking at future prices
    here is the correct and intentional evaluation step.
    """
    timestamps = [b.timestamp for b in all_bars]
    price_map = {b.timestamp: b.close for b in all_bars}

    for dp in data_points:
        t = dp.date if dp.date.tzinfo else dp.date.replace(tzinfo=UTC)

        # Find index of this date in the full bar series
        try:
            t_idx = next(i for i, ts in enumerate(timestamps) if ts >= t)
        except StopIteration:
            continue

        current_price = price_map.get(timestamps[t_idx], dp.price)

        for days, field in [(1, "return_1d"), (7, "return_7d"), (30, "return_30d")]:
            future_idx = t_idx + days
            if future_idx < len(timestamps):
                future_price = price_map[timestamps[future_idx]]
                setattr(dp, field, round((future_price - current_price) / current_price, 6))


# ── Metric computation ────────────────────────────────────────────────────────

def _accuracy(points: list[BacktestDataPoint], return_field: str) -> float | None:
    """
    Directional accuracy: fraction of predictions where
    sign(score - 50) == sign(forward_return).
    Neutral scores (exactly 50) are excluded.
    """
    correct = total = 0
    for dp in points:
        fwd = getattr(dp, return_field)
        if fwd is None or dp.score == 50.0:
            continue
        score_dir = 1 if dp.score > 50 else -1
        ret_dir = 1 if fwd > 0 else (-1 if fwd < 0 else 0)
        if ret_dir != 0:
            total += 1
            if score_dir == ret_dir:
                correct += 1
    return round(correct / total, 4) if total > 0 else None


def _sharpe_and_drawdown(points: list[BacktestDataPoint]) -> tuple[float | None, float | None]:
    """
    Naive long/short strategy: long when score > 50, short otherwise.
    Returns (annualised_sharpe, max_drawdown_pct).
    """
    strategy_returns: list[float] = []
    for dp in points:
        if dp.return_1d is None:
            continue
        direction = 1 if dp.score > 50 else -1
        strategy_returns.append(direction * dp.return_1d)

    if len(strategy_returns) < 5:
        return None, None

    mean_r = statistics.mean(strategy_returns)
    std_r = statistics.stdev(strategy_returns)

    sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else None

    # Max drawdown from cumulative wealth
    wealth = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in strategy_returns:
        wealth *= (1 + r)
        if wealth > peak:
            peak = wealth
        dd = (peak - wealth) / peak
        if dd > max_dd:
            max_dd = dd

    return (
        round(sharpe, 3) if sharpe is not None else None,
        round(-max_dd * 100, 2),
    )


def _calibration(points: list[BacktestDataPoint]) -> list[CalibrationBucket]:
    buckets = []
    def _mean_ret(pts: list[BacktestDataPoint], field: str) -> float | None:
        vals = [getattr(dp, field) for dp in pts if getattr(dp, field) is not None]
        return round(statistics.mean(vals), 5) if vals else None

    for label, lo, hi in _SCORE_BUCKETS:
        bucket_pts = [dp for dp in points if lo <= dp.score < hi or (hi == 100 and dp.score == 100)]
        n = len(bucket_pts)

        buckets.append(CalibrationBucket(
            bucket=label,
            n=n,
            mean_return_1d=_mean_ret(bucket_pts, "return_1d"),
            mean_return_7d=_mean_ret(bucket_pts, "return_7d"),
            mean_return_30d=_mean_ret(bucket_pts, "return_30d"),
        ))
    return buckets


def _compute_metrics(data_points: list[BacktestDataPoint]) -> BacktestMetrics:
    if not data_points:
        return BacktestMetrics(
            n_predictions=0, n_with_1d_data=0, n_with_7d_data=0, n_with_30d_data=0,
            mean_score=0.0, score_std=0.0,
        )

    scores = [dp.score for dp in data_points]
    sharpe, max_dd = _sharpe_and_drawdown(data_points)

    return BacktestMetrics(
        n_predictions=len(data_points),
        n_with_1d_data=sum(1 for dp in data_points if dp.return_1d is not None),
        n_with_7d_data=sum(1 for dp in data_points if dp.return_7d is not None),
        n_with_30d_data=sum(1 for dp in data_points if dp.return_30d is not None),
        mean_score=round(statistics.mean(scores), 2),
        score_std=round(statistics.stdev(scores) if len(scores) > 1 else 0.0, 2),
        accuracy_1d=_accuracy(data_points, "return_1d"),
        accuracy_7d=_accuracy(data_points, "return_7d"),
        accuracy_30d=_accuracy(data_points, "return_30d"),
        sharpe_1d=sharpe,
        max_drawdown_pct=max_dd,
        calibration=_calibration(data_points),
    )


# ── Markdown report ───────────────────────────────────────────────────────────

def _generate_report(
    ticker: str,
    from_date: str,
    to_date: str,
    scoring_version: str,
    metrics: BacktestMetrics,
) -> str:
    def pct(v: float | None) -> str:
        return f"{v*100:.1f}%" if v is not None else "N/A"

    def flt(v: float | None, d: int = 3) -> str:
        return f"{v:.{d}f}" if v is not None else "N/A"

    cal_rows = "\n".join(
        f"| {b.bucket} | {b.n} | {pct(b.mean_return_1d)} | {pct(b.mean_return_7d)} | {pct(b.mean_return_30d)} |"
        for b in metrics.calibration
    )

    return f"""# PulseTrader Backtest Report

**Ticker:** {ticker}
**Period:** {from_date} → {to_date}
**Scoring version:** {scoring_version}
**Predictions:** {metrics.n_predictions}

> Educational use only. Not financial advice.

## Performance Metrics

| Metric | Value |
|--------|-------|
| 1-day directional accuracy | {pct(metrics.accuracy_1d)} |
| 7-day directional accuracy | {pct(metrics.accuracy_7d)} |
| 30-day directional accuracy | {pct(metrics.accuracy_30d)} |
| 1-day Sharpe (long/short) | {flt(metrics.sharpe_1d)} |
| Max drawdown | {flt(metrics.max_drawdown_pct, 1)}% |
| Mean Pulse Score | {metrics.mean_score:.1f} |
| Score std dev | {metrics.score_std:.1f} |

## Score Calibration (Score Bucket → Forward Returns)

| Score Bucket | N | Mean 1d Return | Mean 7d Return | Mean 30d Return |
|-------------|---|----------------|----------------|-----------------|
{cal_rows}

*A well-calibrated score should show higher buckets yielding higher returns.*
"""


# ── Main entry point ──────────────────────────────────────────────────────────

def run_backtest(
    ticker: str,
    all_bars: list[OHLCVBar],
    from_date: str,
    to_date: str,
    step_days: int = 1,
    min_lookback: int = 60,
) -> BacktestResult:
    """
    Run the Pulse Score through historical bars with strict no-lookahead.

    `all_bars` should cover a period starting well before `from_date`
    (for lookback) and ending after `to_date` (for forward returns).

    No news or AI is used — this ensures deterministic, reproducible results.
    """
    job_id = str(uuid.uuid4())[:12]
    scoring_version = get_scoring_config().version
    now = datetime.now(UTC)

    from_dt = datetime.fromisoformat(from_date).replace(tzinfo=UTC)
    to_dt = datetime.fromisoformat(to_date).replace(tzinfo=UTC)

    if not all_bars:
        return BacktestResult(
            job_id=job_id, ticker=ticker,
            from_date=from_date, to_date=to_date, step_days=step_days,
            scoring_version=scoring_version, status="failed",
            created_at=now, error="No OHLCV data available",
        )

    # Scoring dates: bars within the target range, sampled every step_days
    scoring_bars = [b for b in all_bars if from_dt <= b.timestamp <= to_dt]
    if not scoring_bars:
        return BacktestResult(
            job_id=job_id, ticker=ticker,
            from_date=from_date, to_date=to_date, step_days=step_days,
            scoring_version=scoring_version, status="failed",
            created_at=now, error=f"No bars in target range {from_date} → {to_date}",
        )

    scoring_dates = [b.timestamp for b in scoring_bars[::step_days]]
    logger.info("Backtesting %s: %d scoring dates, step=%d", ticker, len(scoring_dates), step_days)

    data_points: list[BacktestDataPoint] = []
    skipped = 0

    for t in scoring_dates:
        # ── STRICT NO-LOOKAHEAD: only past bars ──────────────────────────────
        bars_at_t = _bars_up_to(all_bars, t)

        if len(bars_at_t) < min_lookback:
            skipped += 1
            continue

        try:
            snap = compute_indicators(ticker, bars_at_t)
            if snap is None:
                skipped += 1
                continue

            score = compute_pulse_score(
                ticker=ticker,
                bars=bars_at_t,
                snap=snap,
                news_feed=None,   # no news — deterministic
                ai_score=50.0,    # no AI calls — deterministic + no cost
                mtf_snapshot=None,
            )

            data_points.append(BacktestDataPoint(
                date=t,
                score=score.overall,
                price=bars_at_t[-1].close,
                trend_score=score.trend.score,
                momentum_score=score.momentum.score,
                volatility_score=score.volatility.score,
            ))
        except Exception as exc:
            logger.warning("Score computation failed at %s: %s", t.date(), exc)
            skipped += 1

    if skipped > 0:
        logger.info("Skipped %d/%d dates (insufficient data or errors)",
                    skipped, len(scoring_dates))

    # ── Evaluation: attach forward returns (uses future prices — correct!) ────
    _attach_forward_returns(data_points, all_bars)

    metrics = _compute_metrics(data_points)
    report = _generate_report(ticker, from_date, to_date, scoring_version, metrics)

    return BacktestResult(
        job_id=job_id,
        ticker=ticker,
        from_date=from_date,
        to_date=to_date,
        step_days=step_days,
        scoring_version=scoring_version,
        status="completed",
        created_at=now,
        completed_at=datetime.now(UTC),
        metrics=metrics,
        data_points=data_points,
        report_markdown=report,
    )
