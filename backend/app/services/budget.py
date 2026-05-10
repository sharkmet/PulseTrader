"""
AI cost guardrails — enforce a configurable daily USD spending cap.

Usage:
    from backend.app.services.budget import check_budget_or_raise
    check_budget_or_raise("deep_dive")   # raises BudgetExceeded if over limit

The daily cap is set via DAILY_TOKEN_BUDGET_USD in .env (default $5.00).
Set to 0 to disable entirely.
"""
from __future__ import annotations

from backend.app.config import get_settings
from backend.app.utils.token_logger import get_today_spend


class BudgetExceeded(Exception):
    """Raised when the daily token budget has been reached."""

    def __init__(self, spent: float, budget: float) -> None:
        self.spent = spent
        self.budget = budget
        super().__init__(
            f"Daily AI budget exceeded: ${spent:.4f} spent of ${budget:.2f} limit. "
            f"Resets at midnight UTC. Increase DAILY_TOKEN_BUDGET_USD to raise the cap."
        )


def check_budget_or_raise(purpose: str = "") -> None:
    """
    Query today's SQLite spend and raise BudgetExceeded if at or over the limit.
    No-op if DAILY_TOKEN_BUDGET_USD is 0 (unlimited).
    """
    settings = get_settings()
    budget = settings.daily_token_budget_usd
    if budget <= 0:
        return  # unlimited

    spent = get_today_spend()
    if spent >= budget:
        raise BudgetExceeded(spent=spent, budget=budget)


def budget_status() -> dict:
    """Return current spend vs budget for use in API responses."""
    settings = get_settings()
    budget = settings.daily_token_budget_usd
    spent = get_today_spend()
    remaining = max(0.0, budget - spent) if budget > 0 else None
    return {
        "daily_budget_usd": budget,
        "today_spent_usd": round(spent, 4),
        "remaining_usd": round(remaining, 4) if remaining is not None else None,
        "exceeded": budget > 0 and spent >= budget,
    }
