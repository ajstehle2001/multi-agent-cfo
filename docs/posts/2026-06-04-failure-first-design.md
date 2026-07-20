# Failure-first design for LLM applications: three patterns from a forensic audit

A few weeks ago I spent a weekend doing a forensic audit on a multi-agent options trading system I'd been running on a VPS for several months. It was paper-trading across five strategies, writing daily logs, calling a regime filter on every market open.

What the audit found:

- A duplicate process spawned by PM2 that I didn't know existed
- A double trade-recording bug where every trade got logged twice (sometimes four times, when the duplicate process also ran)
- An ADX calculation returning values between 300 and 858 (the indicator's mathematical range is 0-100)
- A "daily summary" logger that had been silently writing empty files for two weeks
- A legacy equity-trading algorithm still running that I believed had been decommissioned months earlier
- Three inconsistent implementations of trade logging across different modules

By the end of the audit, the 60-day paper-trading validation clock had to be reset to zero. None of the data I'd been collecting was trustworthy. The full audit writeup is at [agent-reliability-audit](https://github.com/ajstehle2001/agent-reliability-audit).

This post is about what I did next.

I rebuilt a different LLM application — a multi-agent CFO platform that generates structured memos for public companies — and baked the lessons in from day one. Three of those lessons turned into patterns I now consider non-negotiable for any LLM application that drives a real side effect (file write, message send, transaction, anything irreversible).

## Pattern 1: The LLM is never trusted to produce valid output

The most basic mistake in LLM tutorials is the assumption that the model returns what you asked for. It often doesn't.

Even with explicit instructions — "output only JSON, no markdown fences, exactly this schema" — production-grade LLM applications must assume the model can:

- Return malformed JSON
- Omit required fields
- Return fields with wrong types
- Return values outside acceptable ranges (a score of 7 on a 1-5 scale)
- Wrap output in markdown fences despite instructions to the contrary
- Generate plausible-looking but semantically incorrect content

The first five are caught by schema validation. The sixth is unavoidable — that's what the second pattern is for.

In the CFO platform, every LLM call is followed immediately by Pydantic validation:

```python
text = response.text.strip()
if text.startswith("```"):
    text = text.removeprefix("```json").removeprefix("```").strip()
    text = text.removesuffix("```").strip()

memo_data = json.loads(text)
memo = CFOMemo.model_validate(memo_data)
```

The `CFOMemo` schema has explicit field constraints:

```python
class CFOMemo(BaseModel):
    executive_summary: str = Field(..., min_length=20)
    financial_highlights: list[str] = Field(..., min_length=1)
    key_risks: list[str] = Field(..., min_length=1)
    recommended_actions: list[str] = Field(..., min_length=1)
```

A 19-character executive summary fails validation. A missing field fails validation. A non-string in `key_risks` fails validation. The rest of the pipeline never sees malformed data.

The result is one trust boundary, clearly located in the code. Reviewers can see exactly where untrusted LLM output becomes typed data. There is no "maybe this dict has the right keys" handling further down. There is no per-consumer ad-hoc validation that might drift over time.

Tests are easy to write because the schema is pure Pydantic — no network, no LLM. The test matrix for the CFO platform covers ten schema cases in well under a second.

## Pattern 2: Humans approve, machines deliver

LLM-as-judge is useful for catching mechanical quality regressions during development. It is not a substitute for human judgment on outbound content.

The CFO platform's judge scores each memo on four dimensions — specificity, grounding, actionability, numeric_honesty — on a 1-5 scale. The output is itself schema-validated:

```python
class JudgeScores(BaseModel):
    specificity: int = Field(..., ge=1, le=5)
    grounding: int = Field(..., ge=1, le=5)
    actionability: int = Field(..., ge=1, le=5)
    numeric_honesty: int = Field(..., ge=1, le=5)
    rationale: str = Field(..., min_length=20)

    @property
    def passes(self) -> bool:
        return (
            self.specificity >= 3
            and self.grounding >= 3
            and self.actionability >= 3
            and self.numeric_honesty >= 3
            and self.mean >= 3.5
        )
```

The judge's decision is advisory. Nothing reaches an output adapter — file write, email, WhatsApp message — without a human approve/reject/revise through the Confirmation Gate.

This is non-negotiable for me when an LLM application has external side effects. The judge catches mechanical quality regressions cheaply. The human catches the bad-but-plausible content that the judge can't, because the judge is also a language model and shares the generator's blind spots.

Architecturally, this means two distinct trust boundaries:

1. Schema validation between LLM output and downstream code
2. Human confirmation between automated processing and external delivery

A reader of the codebase should be able to point at exactly where each boundary lives. In the CFO platform, the first lives in `synthesize_memo` and `judge_memo` (immediately after the LLM call). The second lives in `run_scheduler` (immediately before `output.deliver`).

## Pattern 3: Retry inside the circuit breaker, not the other way around

This is the most technically opinionated pattern, and the one I see most often gotten wrong.

External calls to LLM APIs and data sources need two resilience layers:

- **Retry** for transient errors (timeout, connection error, 5xx, 429)
- **Circuit breaker** for sustained failures (the upstream is genuinely sick and you should stop hammering it)

There are two ways to compose these layers:

**Option A — retry inside breaker.** `breaker.call(retrying_function)`. The breaker sees one logical call, even if that call internally tried three times.

**Option B — breaker inside retry.** `@retry def fn(): breaker.call(...)`. The breaker sees each retry attempt as a separate observation.

Option B is wrong for any failure threshold below about ten. Here's why.

With Option B and a typical `failure_threshold=5`, a single transient blip consumes three breaker observations (the three retry attempts). After two transient blips, the breaker has used 60% of its failure budget on what is, from the application's perspective, one event.

Option A means breaker budget tracks application-level health, not retry mechanics. A transient blip costs zero observations (retry succeeds) or one (retry exhausts and the breaker sees the failure as a single event).

In the CFO platform, this composition looks like:

```python
class EdgarClient:
    @retry(
        retry=retry_if_exception(_is_retryable_edgar_error),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _fetch(self, url: str) -> dict:
        response = self._client.get(url)
        response.raise_for_status()
        return response.json()

    def _get_json(self, url: str) -> dict:
        return EDGAR_BREAKER.call(self._fetch, url)
```

`_fetch` is the retrying inner function. `_get_json` wraps it in the breaker. One breaker observation per logical call.

I verified this empirically when integrating the breaker. With a deliberate 503 error injected into every call: 5 logical attempts × 3 internal retries each = 15 actual HTTP requests, then the breaker tripped and subsequent calls failed with zero HTTP attempts. That's the desired behavior. Under Option B, the breaker would have tripped after 2 logical attempts, which is too sensitive.

The same composition applies to the Anthropic API client, with its own per-host breaker. SEC EDGAR going down should not trip the Anthropic breaker, and vice versa. Each upstream gets its own budget.

The retry predicate also matters. The naive version retries on `httpx.HTTPError`, which is the base class — it includes `HTTPStatusError` for any 4xx or 5xx response. That means a 404 (ticker not found) also triggers three retries with exponential backoff, for an error that's deterministic and won't get better.

The narrow version retries only on 5xx, 429, and connection-class errors:

```python
def _is_retryable_edgar_error(exc):
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code >= 500 or code == 429
    return False
```

A 404 raises immediately. A 503 retries with backoff, then propagates to the breaker if all attempts fail.

## What I'd do differently

Three things I haven't done in this version but would do for a production deployment:

**MCP server for the Confirmation Gate.** Currently the gate is a CLI prompt. For a system that runs unattended, the right interface is a Slack or web-based approval flow exposed via Model Context Protocol. The `ConfirmationAdapter` Protocol I designed accepts this swap with no scheduler changes — that was deliberate.

**Prompt regression CI.** The judge catches quality regressions in synthesized memos, but it doesn't catch the harder problem: a prompt change that introduces subtle hallucinations a judge wouldn't notice. I'd want a CI step that runs a golden-output diff on a fixed set of test prompts whenever a prompt template changes, with human review of the diff before merge.

**Explicit PII boundary documentation.** The current version pulls only SEC-public data. A real fractional CFO product would include private financials, board minutes, internal forecasts. The trust boundary for that data needs to be explicit and documented — what crosses to the LLM, what stays in the database, what gets redacted before the prompt is constructed.

## The repo

The full codebase is at [github.com/ajstehle2001/multi-agent-cfo](https://github.com/ajstehle2001/multi-agent-cfo). The audit story that motivated this rebuild is at [github.com/ajstehle2001/agent-reliability-audit](https://github.com/ajstehle2001/agent-reliability-audit). And for the same discipline applied to a quantitative domain, [structural-commodity-monitor](https://github.com/ajstehle2001/structural-commodity-monitor) implements pre-registered statistical testing, signal-decay governance, and LLM data extraction with validation trust boundaries for a commodity fair-value model: the validation philosophy of this post, pointed at a market instead of a memo.

The `docs/ARCHITECTURE.md` and three Architecture Decision Records in `docs/adr/` are where I tried to make the design choices legible to future readers, including future me. Production thinking about LLM systems is still an underdeveloped genre — most online material covers either the latest model release or basic prompt engineering, with very little on what production-grade reliability actually looks like at the application layer. I'm trying to fill some of that gap one repo at a time.

If you're building LLM applications and want to discuss any of this, everything I ship is at [github.com/ajstehle2001](https://github.com/ajstehle2001).

