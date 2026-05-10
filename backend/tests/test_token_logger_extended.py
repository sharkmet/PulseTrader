"""Coverage tests for token_logger and observability."""
from __future__ import annotations

from unittest.mock import patch

import pytest

# ── token_logger ──────────────────────────────────────────────────────────────

def test_log_usage_writes_jsonl(tmp_path):
    from backend.app.utils.token_logger import log_usage
    log_file = tmp_path / "test_usage.jsonl"
    with patch("backend.app.utils.token_logger.get_settings") as mock_cfg:
        mock_cfg.return_value.token_log_path = str(log_file)
        log_usage(
            purpose="test", ticker="AAPL", model="claude-haiku-4-5-20251001",
            input_tokens=100, output_tokens=50,
        )
    assert log_file.exists()
    import json
    record = json.loads(log_file.read_text().strip())
    assert record["purpose"] == "test"
    assert record["ticker"] == "AAPL"
    assert record["input_tokens"] == 100
    assert record["output_tokens"] == 50
    assert record["estimated_cost_usd"] > 0


def test_log_usage_cost_haiku():
    """Haiku: $0.80/M input + $4.00/M output."""
    from backend.app.utils.token_logger import _compute_cost
    cost = _compute_cost("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
    assert abs(cost - 4.80) < 0.001


def test_log_usage_cost_sonnet():
    """Sonnet: $3.00/M input + $15.00/M output."""
    from backend.app.utils.token_logger import _compute_cost
    cost = _compute_cost("claude-sonnet-4-6", 1_000_000, 0)
    assert abs(cost - 3.00) < 0.001


def test_get_today_spend_returns_float():
    from backend.app.database import init_db
    from backend.app.utils.token_logger import get_today_spend
    init_db()
    result = get_today_spend()
    assert isinstance(result, float)
    assert result >= 0


def test_get_usage_report_structure():
    from backend.app.database import init_db
    from backend.app.utils.token_logger import get_usage_report
    init_db()
    report = get_usage_report()
    assert "today" in report
    assert "this_month" in report
    assert "daily_budget_usd" in report
    assert "budget_status" in report
    assert report["budget_status"] in ("ok", "warning", "exceeded", "unlimited")


def test_get_usage_report_today_structure():
    from backend.app.utils.token_logger import get_usage_report
    report = get_usage_report()
    today = report["today"]
    assert "calls" in today
    assert "input_tokens" in today
    assert "output_tokens" in today
    assert "cost_usd" in today


def test_get_usage_summary_is_alias():
    """get_usage_summary should be an alias for get_usage_report."""
    from backend.app.utils.token_logger import get_usage_report, get_usage_summary
    r1 = get_usage_report()
    r2 = get_usage_summary()
    assert set(r1.keys()) == set(r2.keys())


# ── observability ─────────────────────────────────────────────────────────────

def test_observe_logs_on_success():
    from backend.app.services.observability import observe
    logged = {}

    def fake_persist(**kwargs):
        logged.update(kwargs)

    with patch("backend.app.services.observability._persist") as mock_p:
        with observe("test_source", "test_op", ticker="AAPL"):
            pass
        mock_p.assert_called_once()
        args = mock_p.call_args
        assert args.kwargs["success"] is True
        assert args.kwargs["source"] == "test_source"


def test_observe_logs_failure():
    from backend.app.services.observability import observe
    with patch("backend.app.services.observability._persist") as mock_p:
        with pytest.raises(ValueError):
            with observe("test_source", "test_op"):
                raise ValueError("test error")
        args = mock_p.call_args
        assert args.kwargs["success"] is False
        assert "ValueError" in (args.kwargs["error"] or "")


def test_observe_measures_latency():
    import time

    from backend.app.services.observability import observe
    with patch("backend.app.services.observability._persist") as mock_p:
        with observe("src", "op"):
            time.sleep(0.01)
        latency = mock_p.call_args.kwargs["latency_ms"]
        assert latency >= 0  # positive duration measured


def test_recent_requests_returns_list():
    from backend.app.database import init_db
    from backend.app.services.observability import recent_requests
    init_db()
    result = recent_requests(limit=5)
    assert isinstance(result, list)


def test_source_stats_returns_dict():
    from backend.app.database import init_db
    from backend.app.services.observability import source_stats
    init_db()
    result = source_stats()
    assert isinstance(result, dict)


def test_persist_does_not_raise_on_db_error():
    from backend.app.services.observability import _persist
    # SessionLocal is lazily imported — patch at the database module level
    with patch("backend.app.database.SessionLocal", side_effect=Exception("DB down")):
        _persist(source="x", operation="y", ticker=None, latency_ms=1.0, success=True, error=None)
        # Should not raise — _persist swallows DB failures
