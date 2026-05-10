"""
Token usage logger — writes every Claude API call to:
  1. JSONL file (human-readable, append-only)
  2. SQLite `token_usage_log` table (queryable, used for budget enforcement)

Budget checking reads from SQLite (fast). The JSONL file is a backup/audit trail.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from backend.app.config import get_settings

logger = logging.getLogger(__name__)

# Pricing per million tokens (USD).
_PRICING: dict[str, dict[str, float]] = {
    # Gemini (Google) — free tier up to limits, then paid
    "gemini-2.0-flash":          {"input": 0.075, "output": 0.30},
    "gemini-1.5-flash":          {"input": 0.075, "output": 0.30},
    "gemini-1.5-pro":            {"input": 1.25,  "output": 5.00},
    "gemini-2.0-flash-exp":      {"input": 0.0,   "output": 0.0},  # free experimental
    # Claude (Anthropic)
    "claude-haiku-4-5-20251001": {"input": 0.80,  "output": 4.00},
    "claude-sonnet-4-6":         {"input": 3.00,  "output": 15.00},
    "claude-opus-4-7":           {"input": 15.00, "output": 75.00},
}
_DEFAULT_PRICING = {"input": 0.075, "output": 0.30}  # default to Gemini flash pricing


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = _PRICING.get(model, _DEFAULT_PRICING)
    return (
        input_tokens / 1_000_000 * pricing["input"]
        + output_tokens / 1_000_000 * pricing["output"]
    )


def log_usage(
    *,
    purpose: str,
    ticker: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Log a Claude API call to JSONL + SQLite."""
    cost_usd = _compute_cost(model, input_tokens, output_tokens)
    ts = datetime.now(UTC)

    # JSONL (always)
    settings = get_settings()
    log_path = Path(settings.token_log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": ts.isoformat(),
        "purpose": purpose,
        "ticker": ticker,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": round(cost_usd, 6),
    }
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:
        logger.warning("JSONL token log write failed: %s", exc)

    # SQLite (best-effort)
    try:
        from backend.app.database import SessionLocal
        from backend.app.models.db import TokenUsageLog
        with SessionLocal() as db:
            db.add(TokenUsageLog(
                ts=ts,
                purpose=purpose,
                ticker=ticker or None,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=round(cost_usd, 6),
            ))
            db.commit()
    except Exception as exc:
        logger.warning("SQLite token log write failed: %s", exc)


def get_today_spend() -> float:
    """Return total cost_usd logged today (UTC) from SQLite."""
    try:
        from sqlalchemy import func

        from backend.app.database import SessionLocal
        from backend.app.models.db import TokenUsageLog

        today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        with SessionLocal() as db:
            result = db.query(func.sum(TokenUsageLog.cost_usd)).filter(
                TokenUsageLog.ts >= today_start
            ).scalar()
        return float(result or 0.0)
    except Exception as exc:
        logger.debug("get_today_spend failed: %s — returning 0", exc)
        return 0.0


def _period_usage(since: datetime) -> dict:
    """Aggregate usage since a given datetime."""
    try:
        from sqlalchemy import func

        from backend.app.database import SessionLocal
        from backend.app.models.db import TokenUsageLog

        with SessionLocal() as db:
            rows = db.query(
                func.count(TokenUsageLog.id),
                func.sum(TokenUsageLog.input_tokens),
                func.sum(TokenUsageLog.output_tokens),
                func.sum(TokenUsageLog.cost_usd),
            ).filter(TokenUsageLog.ts >= since).one()

        return {
            "calls": int(rows[0] or 0),
            "input_tokens": int(rows[1] or 0),
            "output_tokens": int(rows[2] or 0),
            "cost_usd": round(float(rows[3] or 0.0), 6),
        }
    except Exception:
        return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}


def get_usage_report() -> dict:
    """Full usage report for the admin endpoint."""
    settings = get_settings()
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    today = _period_usage(today_start)
    month = _period_usage(month_start)

    budget = settings.daily_token_budget_usd
    remaining = max(0.0, budget - today["cost_usd"]) if budget > 0 else float("inf")
    utilization = (today["cost_usd"] / budget * 100) if budget > 0 else 0.0

    if budget <= 0:
        status = "unlimited"
    elif utilization >= 100:
        status = "exceeded"
    elif utilization >= 80:
        status = "warning"
    else:
        status = "ok"

    # Recent calls from SQLite
    recent: list[dict] = []
    try:
        from backend.app.database import SessionLocal
        from backend.app.models.db import TokenUsageLog
        with SessionLocal() as db:
            rows = (
                db.query(TokenUsageLog)
                .order_by(TokenUsageLog.ts.desc())
                .limit(50)
                .all()
            )
            recent = [
                {
                    "ts": r.ts.isoformat(),
                    "purpose": r.purpose,
                    "ticker": r.ticker,
                    "model": r.model,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "cost_usd": r.cost_usd,
                }
                for r in rows
            ]
    except Exception:
        pass

    return {
        "today": today,
        "this_month": month,
        "daily_budget_usd": budget,
        "budget_remaining_usd": round(remaining, 4),
        "budget_utilization_pct": round(utilization, 1),
        "budget_status": status,
        "recent_calls": recent,
    }


# Legacy alias for existing callers
def get_usage_summary() -> dict:
    return get_usage_report()
