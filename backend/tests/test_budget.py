"""
Phase 6 tests — AI cost guardrails, prompt templates, and usage reporting.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.app.services.budget import BudgetExceeded, budget_status, check_budget_or_raise

# ── BudgetExceeded exception ──────────────────────────────────────────────────

def test_budget_exceeded_has_spent_and_budget():
    exc = BudgetExceeded(spent=6.50, budget=5.00)
    assert exc.spent == 6.50
    assert exc.budget == 5.00
    assert "exceeded" in str(exc).lower()


def test_budget_exceeded_is_exception():
    with pytest.raises(BudgetExceeded):
        raise BudgetExceeded(1.0, 0.5)


# ── check_budget_or_raise ─────────────────────────────────────────────────────

def test_check_budget_no_op_when_disabled():
    """Budget check is a no-op when DAILY_TOKEN_BUDGET_USD = 0."""
    with patch("backend.app.services.budget.get_settings") as mock_cfg:
        mock_cfg.return_value.daily_token_budget_usd = 0.0
        check_budget_or_raise("test")  # should not raise


def test_check_budget_raises_when_exceeded():
    with (
        patch("backend.app.services.budget.get_settings") as mock_cfg,
        patch("backend.app.services.budget.get_today_spend", return_value=6.0),
    ):
        mock_cfg.return_value.daily_token_budget_usd = 5.0
        with pytest.raises(BudgetExceeded) as exc_info:
            check_budget_or_raise("deep_dive")
        assert exc_info.value.spent == 6.0
        assert exc_info.value.budget == 5.0


def test_check_budget_passes_when_under_limit():
    with (
        patch("backend.app.services.budget.get_settings") as mock_cfg,
        patch("backend.app.services.budget.get_today_spend", return_value=2.0),
    ):
        mock_cfg.return_value.daily_token_budget_usd = 5.0
        check_budget_or_raise("deep_dive")  # should not raise


def test_check_budget_raises_exactly_at_limit():
    """Spending equal to the limit should also block (>=, not just >)."""
    with (
        patch("backend.app.services.budget.get_settings") as mock_cfg,
        patch("backend.app.services.budget.get_today_spend", return_value=5.0),
    ):
        mock_cfg.return_value.daily_token_budget_usd = 5.0
        with pytest.raises(BudgetExceeded):
            check_budget_or_raise("test")


# ── budget_status ─────────────────────────────────────────────────────────────

def test_budget_status_structure():
    with (
        patch("backend.app.services.budget.get_settings") as mock_cfg,
        patch("backend.app.services.budget.get_today_spend", return_value=1.5),
    ):
        mock_cfg.return_value.daily_token_budget_usd = 5.0
        status = budget_status()
    assert "daily_budget_usd" in status
    assert "today_spent_usd" in status
    assert "remaining_usd" in status
    assert "exceeded" in status


def test_budget_status_not_exceeded_when_under():
    with (
        patch("backend.app.services.budget.get_settings") as mock_cfg,
        patch("backend.app.services.budget.get_today_spend", return_value=1.0),
    ):
        mock_cfg.return_value.daily_token_budget_usd = 5.0
        status = budget_status()
    assert status["exceeded"] is False
    assert status["remaining_usd"] == pytest.approx(4.0, abs=0.01)


def test_budget_status_exceeded_flag():
    with (
        patch("backend.app.services.budget.get_settings") as mock_cfg,
        patch("backend.app.services.budget.get_today_spend", return_value=7.0),
    ):
        mock_cfg.return_value.daily_token_budget_usd = 5.0
        status = budget_status()
    assert status["exceeded"] is True
    assert status["remaining_usd"] == 0.0


def test_budget_status_unlimited_when_zero():
    with (
        patch("backend.app.services.budget.get_settings") as mock_cfg,
        patch("backend.app.services.budget.get_today_spend", return_value=100.0),
    ):
        mock_cfg.return_value.daily_token_budget_usd = 0.0
        status = budget_status()
    assert status["exceeded"] is False
    assert status["remaining_usd"] is None


# ── Prompt templates ──────────────────────────────────────────────────────────

def test_prompt_files_exist():
    """Ensure all expected prompt template files are present."""
    from pathlib import Path
    prompts_dir = Path(__file__).parent.parent / "prompts"
    for name in ("deep_dive", "ai_score", "news_interpreter"):
        assert (prompts_dir / f"{name}.txt").exists(), f"Missing prompt: {name}.txt"


def test_load_prompt_returns_string():
    from backend.app.services.ai_client import _load_prompt
    template = _load_prompt("deep_dive")
    assert isinstance(template, str)
    assert len(template) > 50  # non-trivial content


def test_prompt_contains_placeholders():
    from backend.app.services.ai_client import _load_prompt
    for name in ("deep_dive", "ai_score"):
        template = _load_prompt(name)
        assert "{ticker}" in template, f"{name}.txt missing {{ticker}}"
        assert "{context}" in template, f"{name}.txt missing {{context}}"


def test_render_substitutes_variables():
    from backend.app.services.ai_client import _render
    rendered = _render("ai_score", ticker="AAPL", context="RSI=55")
    assert "AAPL" in rendered
    assert "RSI=55" in rendered
    assert "{ticker}" not in rendered  # substituted


def test_render_includes_disclaimer():
    from backend.app.services.ai_client import _render
    rendered = _render("deep_dive", ticker="BTC-USD", context="price=50000")
    assert "educational" in rendered.lower() or "financial advice" in rendered.lower()


# ── AI client budget integration ──────────────────────────────────────────────

def test_get_ai_score_returns_50_when_budget_exceeded():
    """get_ai_score must not raise — returns 50 when budget is exceeded."""
    from backend.app.services.ai_client import get_ai_score
    with (
        patch("backend.app.services.ai_client.get_settings") as mock_cfg,
        patch("backend.app.services.ai_client.check_budget_or_raise",
              side_effect=BudgetExceeded(5.0, 5.0)),
    ):
        mock_cfg.return_value.has_anthropic_key = True
        result = get_ai_score("AAPL", "RSI=55, sentiment=0.2")
    assert result == 50.0


def test_generate_analysis_returns_fallback_without_key():
    """generate_analysis returns a neutral stub when no AI key is set."""
    from backend.app.services.ai_client import generate_analysis
    with patch("backend.app.services.ai_client.get_settings") as mock_cfg:
        mock_cfg.return_value.has_ai_key = False  # neither Google nor Anthropic
        result = generate_analysis("AAPL", [], None, None)
    assert result.ticker == "AAPL"
    assert result.ai_score == 50.0
    assert result.model_used == "none"


# ── Token logger cost computation ─────────────────────────────────────────────

def test_cost_computed_correctly():
    from backend.app.utils.token_logger import _compute_cost
    # haiku: $0.80/M input, $4.00/M output
    cost = _compute_cost("claude-haiku-4-5-20251001", input_tokens=1_000_000, output_tokens=1_000_000)
    assert abs(cost - 4.80) < 0.01


def test_cost_uses_default_for_unknown_model():
    from backend.app.utils.token_logger import _compute_cost
    cost = _compute_cost("unknown-model-xyz", input_tokens=1_000_000, output_tokens=0)
    # default is Gemini flash pricing: $0.075/M input
    assert cost == pytest.approx(0.075, abs=0.001)
