"""Circuit breaker for external service calls.

States:
  - CLOSED: requests pass through normally; consecutive failures are counted.
  - OPEN: requests fail fast with CircuitOpenError without contacting the
    upstream. Stays open for `cooldown_seconds`.
  - HALF_OPEN: one probe request is allowed through after cooldown.
    Success closes the breaker; failure reopens it for another cooldown.

Why this matters:
  1. We stop hammering an upstream that's already failing — important when
     SEC EDGAR rate-limits or the Anthropic API has a regional outage.
  2. We give the rest of the system immediate feedback rather than making
     callers wait for repeated retry chains to time out.

The breaker is thread-safe — multiple concurrent callers can share the
same instance. State transitions are logged at INFO/WARNING so they show
up in the structured log with run_id and client correlation.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the breaker is open."""

    def __init__(self, name: str, reopens_at: float) -> None:
        self.name = name
        self.reopens_at = reopens_at
        remaining = max(0.0, reopens_at - time.monotonic())
        super().__init__(
            f"Circuit breaker '{name}' is open; retry in {remaining:.1f}s"
        )


@dataclass
class CircuitBreaker:
    """Thread-safe circuit breaker.

    Args:
        name: identifier used in log lines and exception messages.
        failure_threshold: consecutive failures before transitioning
            CLOSED -> OPEN. Default 5.
        cooldown_seconds: how long to stay OPEN before allowing a
            HALF_OPEN probe. Default 60.0.
    """

    name: str
    failure_threshold: int = 5
    cooldown_seconds: float = 60.0
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _reopens_at: float = field(default=0.0, init=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    @property
    def state(self) -> CircuitState:
        """Current circuit state (snapshot, not synchronized with calls in flight)."""
        with self._lock:
            return self._state

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute func through the breaker.

        Raises CircuitOpenError immediately if the breaker is open and the
        cooldown has not elapsed. Any exception raised by func is recorded
        as a failure and re-raised unchanged.
        """
        self._before_call()
        try:
            result = func(*args, **kwargs)
        except Exception:
            self._record_failure()
            raise
        else:
            self._record_success()
            return result

    # --- internals ---------------------------------------------------------

    def _before_call(self) -> None:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if time.monotonic() >= self._reopens_at:
                    logger.info(
                        "Circuit breaker %r OPEN -> HALF_OPEN (probe)",
                        self.name,
                        extra={"breaker": self.name, "transition": "open_to_half_open"},
                    )
                    self._state = CircuitState.HALF_OPEN
                else:
                    raise CircuitOpenError(self.name, self._reopens_at)

    def _record_failure(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                logger.warning(
                    "Circuit breaker %r HALF_OPEN probe failed; reopening for %.1fs",
                    self.name, self.cooldown_seconds,
                    extra={"breaker": self.name, "transition": "half_open_to_open"},
                )
                self._open_circuit()
                return

            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                logger.warning(
                    "Circuit breaker %r tripped: %d consecutive failures; "
                    "CLOSED -> OPEN for %.1fs",
                    self.name, self._failure_count, self.cooldown_seconds,
                    extra={
                        "breaker": self.name,
                        "transition": "closed_to_open",
                        "failure_count": self._failure_count,
                    },
                )
                self._open_circuit()

    def _record_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                logger.info(
                    "Circuit breaker %r HALF_OPEN probe succeeded; closing",
                    self.name,
                    extra={"breaker": self.name, "transition": "half_open_to_closed"},
                )
            self._state = CircuitState.CLOSED
            self._failure_count = 0

    def _open_circuit(self) -> None:
        # Caller already holds self._lock.
        self._state = CircuitState.OPEN
        self._reopens_at = time.monotonic() + self.cooldown_seconds