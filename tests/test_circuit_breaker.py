"""Failure-mode tests for the circuit breaker.

Exercises the state machine across CLOSED, OPEN, and HALF_OPEN
transitions. Time is advanced via monkeypatch so cooldown-dependent
tests are deterministic and complete in milliseconds, not seconds.
"""

from __future__ import annotations

import pytest

from multi_agent_cfo.resilience import circuit_breaker as cb_mod
from multi_agent_cfo.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
)


def _raiser(exc_type: type[BaseException] = RuntimeError, msg: str = "boom"):
    """Return a callable that raises the given exception when called."""
    def f():
        raise exc_type(msg)
    return f


def test_starts_closed():
    breaker = CircuitBreaker(name="t", failure_threshold=3, cooldown_seconds=10.0)
    assert breaker.state == CircuitState.CLOSED


def test_closed_to_open_on_consecutive_failures():
    """N consecutive failures should trip the breaker open."""
    breaker = CircuitBreaker(name="t", failure_threshold=3, cooldown_seconds=10.0)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            breaker.call(_raiser())
    assert breaker.state == CircuitState.OPEN


def test_open_fails_fast_without_invoking_func():
    """While OPEN, calls raise CircuitOpenError without invoking the wrapped function.

    This is the whole point of the breaker — once we know the upstream
    is sick, we should stop wasting time and resources calling it.
    """
    breaker = CircuitBreaker(name="t", failure_threshold=2, cooldown_seconds=10.0)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            breaker.call(_raiser())
    assert breaker.state == CircuitState.OPEN

    call_count = {"n": 0}

    def tracked():
        call_count["n"] += 1
        return "should not be reached"

    with pytest.raises(CircuitOpenError):
        breaker.call(tracked)

    assert call_count["n"] == 0, "Function was invoked despite open breaker"


def test_success_resets_failure_count():
    """A successful call before the threshold resets the consecutive-failure counter.

    The breaker tracks *consecutive* failures, not total. A single success
    in the middle of a run of failures should reset the count so we don't
    trip prematurely on intermittent issues that are already recovering.
    """
    breaker = CircuitBreaker(name="t", failure_threshold=3, cooldown_seconds=10.0)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            breaker.call(_raiser())

    # A success between failures resets the counter
    assert breaker.call(lambda: "ok") == "ok"
    assert breaker.state == CircuitState.CLOSED

    # 2 more failures shouldn't trip — would need 3 consecutive
    for _ in range(2):
        with pytest.raises(RuntimeError):
            breaker.call(_raiser())
    assert breaker.state == CircuitState.CLOSED


def test_open_to_half_open_after_cooldown(monkeypatch):
    """After cooldown elapses, the next call is allowed through as a probe."""
    fake_time = [1000.0]
    monkeypatch.setattr(cb_mod.time, "monotonic", lambda: fake_time[0])

    breaker = CircuitBreaker(name="t", failure_threshold=2, cooldown_seconds=10.0)

    # Trip the breaker
    for _ in range(2):
        with pytest.raises(RuntimeError):
            breaker.call(_raiser())
    assert breaker.state == CircuitState.OPEN

    # Advance simulated time past the cooldown
    fake_time[0] = 1011.0

    # Probe succeeds — breaker should close
    assert breaker.call(lambda: "ok") == "ok"
    assert breaker.state == CircuitState.CLOSED


def test_half_open_probe_failure_reopens(monkeypatch):
    """If the HALF_OPEN probe fails, the breaker reopens for another cooldown.

    This guards against flapping — we don't want to immediately
    reset the failure budget after a single probe failure, because the
    upstream may still be unstable. Reopen and wait again.
    """
    fake_time = [1000.0]
    monkeypatch.setattr(cb_mod.time, "monotonic", lambda: fake_time[0])

    breaker = CircuitBreaker(name="t", failure_threshold=2, cooldown_seconds=10.0)

    # Trip the breaker
    for _ in range(2):
        with pytest.raises(RuntimeError):
            breaker.call(_raiser())
    assert breaker.state == CircuitState.OPEN

    # Advance past cooldown — next call is a probe
    fake_time[0] = 1011.0

    # Probe fails — should reopen
    with pytest.raises(RuntimeError):
        breaker.call(_raiser())
    assert breaker.state == CircuitState.OPEN


def test_circuit_open_error_message_includes_name_and_retry_hint():
    """CircuitOpenError should mention the breaker's name and a retry time hint.

    Without these, debugging an open breaker in production logs is a guessing
    game. The exception is the primary diagnostic surface.
    """
    breaker = CircuitBreaker(name="my-service", failure_threshold=1, cooldown_seconds=10.0)
    with pytest.raises(RuntimeError):
        breaker.call(_raiser())

    with pytest.raises(CircuitOpenError) as exc_info:
        breaker.call(lambda: "x")

    msg = str(exc_info.value)
    assert "my-service" in msg, f"Expected breaker name in error message, got: {msg!r}"
    assert "retry in" in msg.lower(), f"Expected retry hint in error message, got: {msg!r}"