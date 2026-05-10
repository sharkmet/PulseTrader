"""Tests for the TTL in-memory cache."""
from __future__ import annotations

from backend.app.services.cache import TTLCache


def test_set_and_get():
    c = TTLCache()
    c.set("k", "v", ttl=60)
    assert c.get("k") == "v"


def test_get_missing_returns_none():
    c = TTLCache()
    assert c.get("nonexistent") is None


def test_expired_entry_returns_none():
    c = TTLCache()
    c.set("k", "v", ttl=-1)  # negative TTL → already expired
    assert c.get("k") is None


def test_delete_removes_entry():
    c = TTLCache()
    c.set("k", "v", ttl=60)
    c.delete("k")
    assert c.get("k") is None


def test_delete_missing_key_is_noop():
    c = TTLCache()
    c.delete("nonexistent")  # should not raise


def test_clear_removes_all():
    c = TTLCache()
    c.set("a", 1, 60)
    c.set("b", 2, 60)
    c.clear()
    assert c.get("a") is None
    assert c.get("b") is None


def test_len_counts_unexpired():
    c = TTLCache()
    c.set("a", 1, 60)
    c.set("b", 2, 60)
    assert len(c) == 2


def test_evict_expired_removes_stale():
    c = TTLCache()
    c.set("old", 1, ttl=-1)  # negative TTL → already expired
    c.set("new", 2, ttl=60)
    removed = c.evict_expired()
    assert removed == 1
    assert c.get("new") == 2
    assert c.get("old") is None


def test_overwrite_existing_key():
    c = TTLCache()
    c.set("k", "old", 60)
    c.set("k", "new", 60)
    assert c.get("k") == "new"


def test_stores_complex_values():
    c = TTLCache()
    obj = {"list": [1, 2, 3], "nested": {"a": True}}
    c.set("complex", obj, 60)
    assert c.get("complex") == obj


def test_thread_safe_concurrent_writes():
    import threading
    c = TTLCache()
    errors = []

    def write(i: int) -> None:
        try:
            c.set(f"key{i}", i, 60)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(c) == 20
