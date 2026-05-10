"""Tests for the circuit breaker state machine."""
from __future__ import annotations

import time

import pytest

from backend.app.services.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpen,
    State,
    all_states,
    get,
)


class TestCircuitBreaker:
    def _make(self, threshold: int = 3, window: float = 60.0, recovery: float = 0.1) -> CircuitBreaker:
        return CircuitBreaker(
            "test",
            failure_threshold=threshold,
            failure_window=window,
            recovery_timeout=recovery,
        )

    def test_starts_closed(self):
        cb = self._make()
        assert cb.state == State.CLOSED

    def test_success_keeps_closed(self):
        cb = self._make()
        with cb:
            pass  # no exception
        assert cb.state == State.CLOSED

    def test_failures_below_threshold_stay_closed(self):
        cb = self._make(threshold=3)
        for _ in range(2):
            try:
                with cb:
                    raise ValueError("boom")
            except ValueError:
                pass
        assert cb.state == State.CLOSED

    def test_failures_at_threshold_open_circuit(self):
        cb = self._make(threshold=3)
        for _ in range(3):
            try:
                with cb:
                    raise ValueError("boom")
            except ValueError:
                pass
        assert cb.state == State.OPEN

    def test_open_circuit_raises_immediately(self):
        cb = self._make(threshold=1, recovery=60.0)
        try:
            with cb:
                raise ValueError("trigger")
        except ValueError:
            pass
        assert cb.state == State.OPEN

        with pytest.raises(CircuitBreakerOpen):
            with cb:
                pass  # should never get here

    def test_open_transitions_to_half_open_after_timeout(self):
        cb = self._make(threshold=1, recovery=0.05)
        try:
            with cb:
                raise ValueError("trigger")
        except ValueError:
            pass
        assert cb.state == State.OPEN

        time.sleep(0.1)
        # State resolves to HALF_OPEN on next access
        assert cb.state == State.HALF_OPEN

    def test_half_open_success_closes_circuit(self):
        cb = self._make(threshold=1, recovery=0.05)
        try:
            with cb:
                raise ValueError("trigger")
        except ValueError:
            pass
        time.sleep(0.1)
        assert cb.state == State.HALF_OPEN

        with cb:
            pass  # success

        assert cb.state == State.CLOSED

    def test_half_open_failure_reopens_circuit(self):
        cb = self._make(threshold=1, recovery=0.05)
        try:
            with cb:
                raise ValueError("trigger")
        except ValueError:
            pass
        time.sleep(0.1)
        assert cb.state == State.HALF_OPEN

        try:
            with cb:
                raise ValueError("still broken")
        except ValueError:
            pass

        assert cb.state == State.OPEN

    def test_reset_returns_to_closed(self):
        cb = self._make(threshold=1, recovery=60.0)
        try:
            with cb:
                raise ValueError("trigger")
        except ValueError:
            pass
        assert cb.state == State.OPEN
        cb.reset()
        assert cb.state == State.CLOSED

    def test_exception_propagates_through_breaker(self):
        cb = self._make(threshold=5)
        with pytest.raises(RuntimeError, match="propagated"):
            with cb:
                raise RuntimeError("propagated")

    def test_no_exception_does_not_count_as_failure(self):
        cb = self._make(threshold=2)
        for _ in range(10):
            with cb:
                pass
        assert cb.state == State.CLOSED


def test_get_returns_registered_breaker():
    cb = get("yfinance")
    assert cb is not None
    assert isinstance(cb, CircuitBreaker)


def test_get_creates_new_for_unknown():
    cb = get("brand_new_source_xyz_abc")
    assert cb is not None
    assert cb.state == State.CLOSED


def test_all_states_returns_dict():
    states = all_states()
    assert isinstance(states, dict)
    assert "yfinance" in states
    assert states["yfinance"] in ("closed", "open", "half_open")
