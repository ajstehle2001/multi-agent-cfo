"""LLM client wrapper for Claude API calls.

Provides a typed, retry-resilient interface to the Anthropic Claude API.
All Intelligence Layer components use this wrapper rather than calling
the Anthropic SDK directly — this is the seam where future adapters
(OpenAI, Bedrock, Vertex, local) plug in by implementing the LLM Protocol.

Resilience layers:
- The Anthropic SDK itself retries transient errors (you can see this
  in the JSONL log: idempotency_key prefix 'stainless-python-retry-...').
- Tenacity adds a second outer retry loop with exponential backoff,
  scoped to the same transient error classes (connection, timeout,
  rate limit, 5xx). Deterministic errors (auth, bad request) skip
  retry entirely.
- Module-level circuit breaker that fails fast when the Anthropic API
  is consistently unavailable, so we stop wasting time on retries when
  the upstream is sick.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import anthropic
from dotenv import load_dotenv
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from multi_agent_cfo.resilience.circuit_breaker import CircuitBreaker

# Load environment variables from .env once at module import.
load_dotenv()


DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 4096

# Module-level breaker shared across all LLMClient instances. The
# Anthropic API is global, so any LLMClient observing failures should
# immediately protect every other LLMClient in the process.
ANTHROPIC_BREAKER = CircuitBreaker(
    name="anthropic-api",
    failure_threshold=5,
    cooldown_seconds=60.0,
)


@dataclass(frozen=True)
class LLMResponse:
    """Structured response from an LLM call.

    Frozen so downstream code can treat it as a value object and pass it
    through pipelines without worrying about mutation.
    """

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str


class LLM(Protocol):
    """Protocol for LLM clients that produce structured completions.

    Any class providing a `complete(*, system, user, max_tokens)` method
    that returns an LLMResponse satisfies this protocol — including future
    adapters for OpenAI, Vertex AI, Bedrock, or local models served via
    Ollama or vLLM.

    The synthesize_memo and judge_memo functions type-hint their `llm`
    parameter as `LLM` rather than the concrete LLMClient, so swapping
    the provider requires no changes to caller code.
    """

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...


class LLMClient:
    """Thin wrapper around the Anthropic SDK with retry and breaker layers.

    Responsibilities:
    - Centralize model selection and configuration
    - Provide exponential-backoff retry on transient API failures
    - Fail fast via circuit breaker when the API is consistently unavailable
    - Return a typed response object instead of the raw SDK message
    - Define a clean seam for swapping the underlying LLM provider later
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Add it to your .env file or pass api_key="
            )
        self._client = anthropic.Anthropic(api_key=resolved_key)
        self.model = model
        self.max_tokens = max_tokens

    @retry(
        retry=retry_if_exception_type(
            (
                anthropic.APIConnectionError,
                anthropic.APITimeoutError,
                anthropic.RateLimitError,
                anthropic.InternalServerError,
            )
        ),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _complete_inner(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Inner completion call with retry. Wrapped by complete() which adds the breaker."""
        message = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens or self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )

        # The SDK returns content as a list of blocks; for single-turn
        # responses we expect TextBlocks. Join in case there are multiple.
        text_blocks = [b.text for b in message.content if b.type == "text"]
        text = "\n".join(text_blocks)

        return LLMResponse(
            text=text,
            model=message.model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            stop_reason=message.stop_reason or "unknown",
        )

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Send a single-turn completion request to Claude through retry + breaker.

        Retries up to 3 times with exponential backoff on transient failures
        (network errors, rate limits, 5xx). Does NOT retry on auth or
        bad-request errors — those need human attention. After 5 consecutive
        logical failures (each potentially containing multiple retry attempts),
        the circuit breaker opens for 60 seconds.
        """
        return ANTHROPIC_BREAKER.call(
            self._complete_inner,
            system=system,
            user=user,
            max_tokens=max_tokens,
        )