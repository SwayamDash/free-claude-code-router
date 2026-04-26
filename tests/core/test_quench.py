"""Unit tests for the in-memory chain-fallback quench registry."""

from __future__ import annotations

import threading
import time

import pytest

from core.quench import QuenchRegistry, get_registry


@pytest.fixture
def registry() -> QuenchRegistry:
    """Fresh registry per test (avoids the process-wide singleton)."""
    return QuenchRegistry()


def test_quench_marks_model_unavailable(registry):
    registry.quench("provider/model-a", ttl=60.0, reason="RateLimitError")
    assert registry.is_quenched("provider/model-a")


def test_unknown_model_is_not_quenched(registry):
    assert registry.is_quenched("provider/never-quenched") is False
    assert registry.remaining("provider/never-quenched") == 0.0


def test_quench_with_zero_ttl_is_noop(registry):
    registry.quench("provider/x", ttl=0.0, reason="oops")
    assert registry.is_quenched("provider/x") is False


def test_quench_with_negative_ttl_is_noop(registry):
    registry.quench("provider/x", ttl=-1.0, reason="oops")
    assert registry.is_quenched("provider/x") is False


def test_quench_with_empty_model_ref_is_noop(registry):
    registry.quench("", ttl=60.0, reason="oops")
    # No entry should be recorded; snapshot stays empty.
    assert registry.snapshot() == []


def test_remaining_decreases_over_time(registry, monkeypatch):
    """``remaining()`` reflects wall-clock cooldown."""
    fake_now = [1000.0]

    def fake_monotonic():
        return fake_now[0]

    monkeypatch.setattr(time, "monotonic", fake_monotonic)
    registry.quench("provider/x", ttl=60.0, reason="r")
    assert registry.remaining("provider/x") == pytest.approx(60.0, abs=0.01)

    fake_now[0] += 30.0
    assert registry.remaining("provider/x") == pytest.approx(30.0, abs=0.01)


def test_quench_expires_after_ttl(registry, monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: fake_now[0])

    registry.quench("provider/x", ttl=10.0, reason="r")
    assert registry.is_quenched("provider/x") is True

    fake_now[0] += 11.0
    assert registry.is_quenched("provider/x") is False
    # Expired entry should be lazily evicted.
    assert registry.snapshot() == []


def test_quench_overwrites_existing_entry(registry, monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: fake_now[0])

    registry.quench("provider/x", ttl=10.0, reason="first")
    registry.quench("provider/x", ttl=60.0, reason="second")
    # New deadline replaces the old one.
    assert registry.remaining("provider/x") == pytest.approx(60.0, abs=0.01)
    snapshot = registry.snapshot()
    assert len(snapshot) == 1
    assert snapshot[0].reason == "second"


def test_snapshot_returns_only_active_entries(registry, monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: fake_now[0])

    registry.quench("provider/short", ttl=5.0, reason="a")
    registry.quench("provider/long", ttl=600.0, reason="b")

    snapshot = registry.snapshot()
    refs = {e.model_ref for e in snapshot}
    assert refs == {"provider/short", "provider/long"}

    fake_now[0] += 6.0
    snapshot = registry.snapshot()
    refs = {e.model_ref for e in snapshot}
    assert refs == {"provider/long"}


def test_clear_removes_all_entries(registry):
    registry.quench("provider/a", ttl=60.0, reason="r")
    registry.quench("provider/b", ttl=60.0, reason="r")
    assert len(registry.snapshot()) == 2

    registry.clear()
    assert registry.snapshot() == []
    assert registry.is_quenched("provider/a") is False


def test_concurrent_writes_do_not_corrupt_state(registry):
    """Stress the threading.Lock with parallel quench/check writers."""
    refs = [f"provider/m{i}" for i in range(50)]

    def writer(ref: str) -> None:
        for _ in range(100):
            registry.quench(ref, ttl=60.0, reason="threadtest")
            registry.is_quenched(ref)

    threads = [threading.Thread(target=writer, args=(ref,)) for ref in refs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    snapshot = registry.snapshot()
    assert len(snapshot) == len(refs)
    assert {e.model_ref for e in snapshot} == set(refs)


def test_get_registry_returns_module_singleton():
    """The module-level ``get_registry()`` always returns the same instance."""
    a = get_registry()
    b = get_registry()
    assert a is b
