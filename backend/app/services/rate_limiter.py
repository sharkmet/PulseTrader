"""
Token-bucket rate limiters — one per external data source.

Each bucket has a capacity (burst) and a refill rate (sustained).
`acquire()` blocks until a token is available, up to `timeout` seconds.
Thread-safe via a lock; safe to call from scheduler threads.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)


class TokenBucket:
    """Leaky-bucket rate limiter. Thread-safe."""

    def __init__(self, capacity: float, refill_rate: float, name: str = "") -> None:
        """
        capacity:     maximum tokens (controls burst size)
        refill_rate:  tokens added per second (controls sustained rate)
        """
        self._capacity = capacity
        self._tokens = capacity  # start full
        self._refill_rate = refill_rate
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()
        self._name = name

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        added = elapsed * self._refill_rate
        self._tokens = min(self._capacity, self._tokens + added)
        self._last_refill = now

    def acquire(self, timeout: float = 10.0) -> bool:
        """
        Consume one token. Blocks until available or timeout expires.
        Returns True if acquired, False if timed out.
        """
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
            wait = min(1.0 / self._refill_rate, deadline - time.monotonic())
            if wait <= 0:
                logger.warning("Rate limiter timeout for source '%s'", self._name)
                return False
            time.sleep(wait)

    def available(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens


# ── Per-source limiters ────────────────────────────────────────────────────────
# Tune these per source's documented or observed rate limits.

_buckets: dict[str, TokenBucket] = {
    "yfinance":   TokenBucket(capacity=10, refill_rate=0.5,  name="yfinance"),   # ~30/min
    "coingecko":  TokenBucket(capacity=5,  refill_rate=0.33, name="coingecko"),  # ~20/min free
    "binance":    TokenBucket(capacity=20, refill_rate=5.0,  name="binance"),    # 300/min public
    "fred":       TokenBucket(capacity=10, refill_rate=2.0,  name="fred"),       # 120/min
    "newsapi":    TokenBucket(capacity=2,  refill_rate=0.03, name="newsapi"),    # ~2/min (100/day)
    "rss":        TokenBucket(capacity=10, refill_rate=1.0,  name="rss"),        # relaxed
}


def acquire(source: str, timeout: float = 10.0) -> bool:
    """Acquire a rate-limit token for the named source. Returns False on timeout."""
    bucket = _buckets.get(source)
    if bucket is None:
        return True  # unknown source: don't block
    return bucket.acquire(timeout=timeout)


def available_tokens(source: str) -> float:
    bucket = _buckets.get(source)
    return bucket.available() if bucket else float("inf")
