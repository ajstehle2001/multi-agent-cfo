# ADR 0003: Pydantic schema validation as the LLM trust boundary

## Status

Accepted (2026-06-04, v0.3).

## Context

LLM output is non-deterministic and untrusted. Even with explicit instructions ("output only JSON, no markdown fences, exactly this schema"), production-grade LLM applications must assume the model can:

- Return malformed JSON (truncated, invalid characters)
- Omit required fields
- Return fields with wrong types (string where int expected)
- Return values outside acceptable ranges (e.g., a score of 7 on a 1-5 scale)
- Wrap output in markdown fences despite instructions
- Generate plausible-looking but semantically incorrect content (this is unavoidable; the schema layer is not a remedy)

Downstream code (the Confirmation Gate, output adapters, the test matrix) needs a guarantee that whatever it receives conforms to a known shape. The question is where that guarantee lives.

## Decision

Place a Pydantic schema validation step immediately after every LLM call. Define `CFOMemo` and `JudgeScores` as `pydantic.BaseModel` subclasses with explicit field constraints (`min_length`, `ge`, `le`). Call `Model.model_validate(json.loads(text))` after stripping defensive markdown fences. Any validation failure raises `pydantic.ValidationError`, which propagates up to the scheduler where it is caught and recorded as a per-client failure.

## Consequences

**Positive:**

- **One trust boundary, clearly located.** Reviewers see exactly where untrusted LLM output becomes trusted typed data. There is no "maybe this dict has the right keys" code downstream.
- **Field constraints are documented in code.** `min_length=20` on the executive summary, `ge=1, le=5` on judge scores — these aren't just runtime checks, they're machine-readable specification.
- **Errors are descriptive.** Pydantic validation errors name the field, the constraint, and the actual value. When debugging an LLM output regression, the error message is actionable.
- **Tests are easy to write.** Schema tests are pure Pydantic — no network, no LLM. The test matrix covers ten schema cases in well under a second.

**Negative:**

- **Strict validation can reject borderline outputs that a human would accept.** A 19-character executive summary fails validation even though it might be informative. Mitigated by setting constraints conservatively and treating validation failures as a signal that prompt engineering needs another pass.
- **Pydantic V2's `model_validate` raises a fairly verbose error.** Caller error handling needs to extract the readable parts. Mitigated by the scheduler's `except Exception as exc: logger.error(...)` pattern, which captures the full traceback.

## Alternatives considered

**Hand-rolled validation per consumer.** Rejected — multiplies the validation surface, opens drift between consumers, no machine-readable schema.

**Schema-first prompting with `response_format` SDK features.** Considered, partially adopted: the LLM prompt explicitly states the JSON schema, but SDK-side enforcement is provider-specific and would couple us to one vendor. Pydantic validation works regardless of the LLM provider.

**Trust the model.** Rejected for production use. Acceptable for early prototyping; not acceptable once the output drives a side effect (file write, message send).
