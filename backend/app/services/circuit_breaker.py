"""
Simple circuit breaker — three-state machine per data source.

States:
  CLOSED    — normal operation, calls pass through
  OPEN      — source is failing; calls are rejected immediately for `recovery_timeout` s
  HALF_OPEN — probe: one test call allowed; success → CLOSED, failure → OPEN

Usage:
    cb = CircuitBreaker("yfinance")
    with cb:
        result = call_external_api()
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from enum import Enum

logger = logging.getLogger(__name__)


class State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpen(Exception):
    """Raised when a call is rejected because the circuit is open."""


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        failure_window: float = 60.0,
        recovery_timeout: float = 60.0,
    ) -> None:
        """
        name:               source identifier for logging
        failure_threshold:  number of failures in `failure_window` seconds to open
        failure_window:     sliding window for failure counting (seconds)
        recovery_timeout:   seconds to stay OPEN before trying HALF_OPEN
        """
        self._name = name
        self._threshold = failure_threshold
        self._window = failure_window
        self._recovery = recovery_timeout
        self._state = State.CLOSED
        self._failures: deque[float] = deque()  # timestamps of recent failures
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> State:
        with self._lock:
            return self._resolved_state()

    def _resolved_state(self) -> State:
        """Transition OPEN → HALF_OPEN if recovery timeout elapsed."""
        if (
            self._state == State.OPEN
            and self._opened_at is not None
            and time.monotonic() - self._opened_at >= self._recovery
        ):
            self._state = State.HALF_OPEN
            logger.info("CircuitBreaker '%s': OPEN → HALF_OPEN (probing)", self._name)
        return self._state

    def _record_failure(self) -> None:
        now = time.monotonic()
        self._failures.append(now)
        # Prune old failures outside the window
        cutoff = now - self._window
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()

        recent = len(self._failures)
        if self._state == State.HALF_OPEN or recent >= self._threshold:
            self._state = State.OPEN
            self._opened_at = now
            self._failures.clear()
            logger.warning(
                "CircuitBreaker '%s': → OPEN (failures=%d in %.0fs window)",
                self._name, recent, self._window,
            )

    def _record_success(self) -> None:
        if self._state == State.HALF_OPEN:
            self._state = State.CLOSED
            self._failures.clear()
            logger.info("CircuitBreaker '%s': HALF_OPEN → CLOSED", self._name)

    def __enter__(self) -> CircuitBreaker:
        with self._lock:
            state = self._resolved_state()
            if state == State.OPEN:
                raise CircuitBreakerOpen(
                    f"Circuit breaker '{self._name}' is OPEN — source temporarily disabled"
                )
        return self

    def __exit__(self, exc_type: type | None, exc_val: Exception | None, tb: object) -> bool:
        with self._lock:
            if exc_val is not None:
                self._record_failure()
            else:
                self._record_success()
        return False  # never suppress exceptions

    def reset(self) -> None:
        """Manually close the breaker (useful in tests)."""
        with self._lock:
            self._state = State.CLOSED
            self._failures.clear()
            self._opened_at = None


# ── Per-source breakers ────────────────────────────────────────────────────────

_breakers: dict[str, CircuitBreaker] = {
    "yfinance":  CircuitBreaker("yfinance",  failure_threshold=5, failure_window=60, recovery_timeout=60),
    "coingecko": CircuitBreaker("coingecko", failure_threshold=5, failure_window=60, recovery_timeout=120),
    "binance":   CircuitBreaker("binance",   failure_threshold=5, failure_window=60, recovery_timeout=60),
    "fred":      CircuitBreaker("fred",      failure_threshold=3, failure_window=120, recovery_timeout=300),
    "newsapi":   CircuitBreaker("newsapi",   failure_threshold=3, failure_window=120, recovery_timeout=300),
    "rss":       CircuitBreaker("rss",       failure_threshold=3, failure_window=60,  recovery_timeout=60),
}


def get(source: str) -> CircuitBreaker:
    """Return the circuit breaker for the named source. Creates one if not registered."""
    if source not in _breakers:
        _breakers[source] = CircuitBreaker(source)
    return _breakers[source]


def all_states() -> dict[str, str]:
    return {name: cb.state.value for name, cb in _breakers.items()}
