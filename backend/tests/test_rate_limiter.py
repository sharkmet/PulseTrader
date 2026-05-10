"""Tests for the token-bucket rate limiter."""
from __future__ import annotations

import time

from hypothesis import given, settings
from hypothesis import strategies as st

from backend.app.services.rate_limiter import TokenBucket, acquire, available_tokens


class TestTokenBucket:
    def test_starts_full(self):
        bucket = TokenBucket(capacity=10, refill_rate=1.0, name="test")
        assert bucket.available() >= 9.9  # allow tiny float drift

    def test_acquire_decrements_tokens(self):
        bucket = TokenBucket(capacity=10, refill_rate=1.0, name="test")
        before = bucket.available()
        bucket.acquire()
        after = bucket.available()
        assert after < before

    def test_acquire_returns_true_when_tokens_available(self):
        bucket = TokenBucket(capacity=10, refill_rate=1.0, name="test")
        result = bucket.acquire()
        assert result is True

    def test_empty_bucket_times_out(self):
        # Zero refill rate so it never refills
        bucket = TokenBucket(capacity=1, refill_rate=0.001, name="test")
        bucket.acquire()  # drain the one token
        start = time.monotonic()
        result = bucket.acquire(timeout=0.05)  # should time out fast
        elapsed = time.monotonic() - start
        assert result is False
        assert elapsed < 0.5  # should not hang

    def test_refills_over_time(self):
        bucket = TokenBucket(capacity=2, refill_rate=100.0, name="test")
        bucket.acquire()
        bucket.acquire()  # drain both
        time.sleep(0.02)  # 100 tokens/s → ~2 tokens in 20ms
        assert bucket.available() >= 1.0

    def test_capacity_is_not_exceeded(self):
        bucket = TokenBucket(capacity=5, refill_rate=1000.0, name="test")
        time.sleep(0.05)  # would produce 50 tokens at this rate
        assert bucket.available() <= 5.0 + 0.01  # capped at capacity

    def test_multiple_acquires_drain_sequentially(self):
        bucket = TokenBucket(capacity=5, refill_rate=0.001, name="test")
        results = [bucket.acquire(timeout=0.01) for _ in range(6)]
        assert results[:5] == [True] * 5
        assert results[5] is False  # 6th should time out


def test_acquire_unknown_source_returns_true():
    """Unknown sources should not block."""
    result = acquire("nonexistent_source_xyz")
    assert result is True


def test_available_tokens_known_source():
    tokens = available_tokens("yfinance")
    assert tokens >= 0


def test_available_tokens_unknown_source():
    tokens = available_tokens("nonexistent_source_xyz")
    assert tokens == float("inf")


@given(capacity=st.floats(min_value=1, max_value=100), refill=st.floats(min_value=0.1, max_value=1000))
@settings(max_examples=30, deadline=1000)
def test_available_never_exceeds_capacity(capacity: float, refill: float):
    bucket = TokenBucket(capacity=capacity, refill_rate=refill, name="hyp")
    assert bucket.available() <= capacity + 0.01
