# ADR 0002: Retry inside circuit breaker, not outside

## Status

Accepted (2026-06-04, v0.3).

## Context

The Intelligence Layer makes external HTTP calls to two upstreams: SEC EDGAR and the Anthropic API. Both are wrapped with two resilience layers:

- **Tenacity retry** — automatic retry on transient errors (timeout, connection error, 5xx, 429) with exponential backoff. Up to 3 attempts per logical call.
- **Circuit breaker** — fail-fast protection after N consecutive failures, with a cooldown before allowing a probe.

There is a meaningful choice about how these layers compose:

- **Option A — retry inside breaker**: `breaker.call(retrying_function)`. The breaker sees one logical call, even if that call internally tried 3 times.
- **Option B — breaker inside retry**: `@retry def fn(): breaker.call(...)`. The breaker sees each retry attempt as a separate observation.

## Decision

Implement Option A: retry is wrapped by the breaker. One logical call = one breaker observation.

## Consequences

**Positive:**

- **Breaker budget reflects application-level health, not retry mechanics.** Five logical calls failing in a row means the upstream is meaningfully sick — flip the breaker. Five retry attempts failing in a row could be a single transient blip — that should not trip the breaker.
- **Behavior under transient errors is predictable.** With `failure_threshold=5` and 3 retry attempts per call, a single transient blip costs the breaker zero (retry succeeds) or one (retry exhausts). Under Option B the same blip would cost three observations — burning 60% of the budget on one event.
- **Test reasoning is clearer.** "This call failed at the application level" is a sharper assertion than "this call's third retry failed."

**Negative:**

- **Failures take longer to surface when the breaker is going to trip anyway.** If the upstream is genuinely down, each logical call to a CLOSED breaker still burns through its retry budget (~3 attempts × exponential backoff) before counting as one breaker failure. Total wall-clock time to trip the breaker is therefore longer under Option A than Option B. Mitigated by short retry timeouts and modest retry counts.

## Alternatives considered

**No retry, breaker only.** Rejected — transient blips would trip the breaker too eagerly, hurting availability.

**No breaker, retry only.** Rejected — when an upstream is genuinely down, every request would still consume retry budget and wall-clock time before failing. The breaker provides immediate feedback to callers.

## Evidence

The test `tests/test_circuit_breaker.py::test_open_fails_fast_without_invoking_func` verifies that an OPEN breaker rejects calls without invoking the wrapped function. The live verification in commit `b548406` demonstrates the composition working in practice: 5 logical calls × 3 internal retries = 15 actual HTTP attempts, then the breaker trips and subsequent calls fail with zero HTTP attempts.
