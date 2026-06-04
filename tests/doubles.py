"""Test doubles for failure-mode tests.

Programmable fakes for LLM, EDGAR, confirmation gate, and output adapter
so scheduler integration tests can exercise the full pipeline without
hitting any network and with full control over which steps succeed or fail.
"""

from __future__ import annotations

import json

from multi_agent_cfo.gate.gate import (
    ConfirmationAdapter,
    Decision,
    GateResponse,
)
from multi_agent_cfo.intelligence.edgar import CompanyInfo
from multi_agent_cfo.intelligence.llm import LLMResponse
from multi_agent_cfo.intelligence.synthesis import CFOMemo, SynthesisResult


# ---- Builders -------------------------------------------------------------


def make_company(ticker: str = "TEST", name: str = "Test Co.") -> CompanyInfo:
    return CompanyInfo(
        ticker=ticker.upper(),
        cik="0000000001",
        name=name,
        sic="9999",
        sic_description="Test Industry",
    )


def make_memo() -> CFOMemo:
    return CFOMemo(
        executive_summary="A reasonably specific executive summary for this fixture.",
        financial_highlights=["Revenue trend is stable", "Margins are in expected range"],
        key_risks=["Macro headwind risk", "Competitor pressure"],
        recommended_actions=["Investigate margin drivers", "Stress test downside"],
    )


def make_memo_json_response() -> LLMResponse:
    """LLMResponse whose text is a valid CFOMemo JSON payload."""
    return LLMResponse(
        text=make_memo().model_dump_json(),
        model="fake-synthesis-model",
        input_tokens=100,
        output_tokens=200,
        stop_reason="end_turn",
    )


def make_judge_json_response(
    specificity: int = 4,
    grounding: int = 4,
    actionability: int = 4,
    numeric_honesty: int = 4,
) -> LLMResponse:
    """LLMResponse whose text is a valid JudgeScores JSON payload."""
    payload = {
        "specificity": specificity,
        "grounding": grounding,
        "actionability": actionability,
        "numeric_honesty": numeric_honesty,
        "rationale": "A reasonable explanation of the scoring rationale for testing.",
    }
    return LLMResponse(
        text=json.dumps(payload),
        model="fake-judge-model",
        input_tokens=300,
        output_tokens=50,
        stop_reason="end_turn",
    )


# ---- Doubles --------------------------------------------------------------


class FakeLLM:
    """Programmable LLM satisfying the LLM Protocol.

    Each call to complete() pops the next item from `responses`:
      - LLMResponse → returned
      - Exception instance → raised

    Records every call's arguments in `calls` for later assertion.
    """

    def __init__(
        self,
        responses: list[LLMResponse | Exception] | None = None,
    ) -> None:
        self.responses: list[LLMResponse | Exception] = list(responses or [])
        self.calls: list[dict] = []

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        if not self.responses:
            raise RuntimeError("FakeLLM ran out of scripted responses")
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeEdgar:
    """Programmable EDGAR client.

    `companies` maps ticker → CompanyInfo. Tickers not in the map raise
    ValueError (matching real EdgarClient behavior).
    """

    def __init__(self, companies: dict[str, CompanyInfo] | None = None) -> None:
        self.companies: dict[str, CompanyInfo] = {
            k.upper(): v for k, v in (companies or {}).items()
        }
        self.closed = False

    def lookup_company(self, ticker: str) -> CompanyInfo:
        normalized = ticker.upper()
        if normalized not in self.companies:
            raise ValueError(f"Ticker '{ticker}' not found")
        return self.companies[normalized]

    def close(self) -> None:
        self.closed = True


class ProgrammableConfirmAdapter:
    """Returns scripted decisions in order from a queue.

    Records each invocation's ticker in `calls` for assertions.
    """

    def __init__(self, decisions: list[Decision]) -> None:
        self.decisions: list[Decision] = list(decisions)
        self.calls: list[str] = []

    def confirm(self, synthesis: SynthesisResult) -> GateResponse:
        self.calls.append(synthesis.company.ticker)
        if not self.decisions:
            raise RuntimeError("ProgrammableConfirmAdapter ran out of decisions")
        decision = self.decisions.pop(0)
        return GateResponse(decision=decision, notes="")


class RecordingOutput:
    """OutputAdapter that records every delivery attempt.

    Optionally raises on every deliver() call to exercise the
    output-failure-is-non-fatal path.
    """

    def __init__(self, raise_on_deliver: bool = False) -> None:
        self.delivered: list[str] = []
        self.raise_on_deliver = raise_on_deliver

    def deliver(self, synthesis: SynthesisResult) -> None:
        if self.raise_on_deliver:
            raise IOError("Simulated output adapter failure")
        self.delivered.append(synthesis.company.ticker)