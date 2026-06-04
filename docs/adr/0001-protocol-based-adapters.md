# ADR 0001: Protocol-based adapter interfaces

## Status

Accepted (2026-06-04, v0.3).

## Context

The system has three interchange points where a concrete implementation may need to be swapped: the LLM provider (Anthropic / OpenAI / Bedrock / local), the output channel (file / WhatsApp / email / Slack), and the confirmation surface (CLI / web / Slack). Each needs a stable interface that doesn't leak provider-specific details.

Python offers two idiomatic ways to express an interface:

1. **Abstract Base Class (`abc.ABC`)** — interface defined by inheritance. Concrete classes inherit from the ABC and override abstract methods. The type system enforces the contract via `isinstance` checks.
2. **Structural typing (`typing.Protocol`)** — interface defined by shape. Any class providing the right method signatures satisfies the Protocol without inheriting from anything. Static type checkers verify conformance at the call site.

## Decision

Use `typing.Protocol` for all three adapter interfaces (`LLM`, `OutputAdapter`, `ConfirmationAdapter`).

## Consequences

**Positive:**

- **No inheritance coupling.** A user implementing `OutputAdapter` does not need to import `OutputAdapter` from this package. They just write a class with the right method, and it works. This matters for third-party integrations that don't want to take a dependency on our package types.
- **Tests don't fight the type system.** The `FakeLLM` in `tests/doubles.py` does not inherit from anything. It simply provides a `complete(...)` method, and Python's structural typing accepts it wherever an `LLM` is expected. With ABC, the fake would need to inherit, complicating the test double surface.
- **Provider SDKs aren't always shaped like our ABC.** A future adapter wrapping a different SDK can provide the Protocol method by delegating internally — no awkward `super().__init__()` calls or method-resolution-order quirks.

**Negative:**

- **Less explicit at runtime.** `isinstance(obj, LLM)` only works on a Protocol if decorated with `@runtime_checkable`. We did not apply that decorator because the check would only verify method names exist, not signatures — a weak guarantee. Mistakes are caught by static analysis at the call site instead.
- **Documentation lives in docstrings.** With an ABC, abstract methods document the interface in one place. With a Protocol, the contract lives in the Protocol class plus the docstrings of implementing classes. Mitigated by treating the Protocol definition as the single source of truth for method signatures.

## Alternatives considered

**Mix: ABC for `OutputAdapter`, Protocol for `LLM`.** Rejected — inconsistency between adapter layers would create cognitive load with no upside.

**Duck typing with no formal interface.** Rejected — the Protocol makes the contract visible to readers and type checkers without imposing inheritance.
