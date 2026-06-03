"""Human-in-the-loop confirmation gate.

The trust boundary of the system. Before any synthesized memo is delivered,
it is surfaced to a human reviewer via a ConfirmationAdapter. The human
approves, rejects, or requests revision. Nothing ships without explicit
approval — that's the central design assumption of multi-agent-cfo.

This module defines the adapter Protocol and ships a ConsoleAdapter as the
default implementation. Other adapters (Slack, WhatsApp, web UI) plug in
behind the same Protocol without touching the orchestration layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from multi_agent_cfo.intelligence.synthesis import SynthesisResult


class Decision(Enum):
    """Possible outcomes of a confirmation review."""

    APPROVE = "approve"
    REJECT = "reject"
    REVISE = "revise"


@dataclass(frozen=True)
class GateResponse:
    """Result of submitting a memo to the confirmation gate.

    Notes are optional human input that becomes part of the audit trail.
    For REVISE, they can feed back into the next synthesis attempt.
    """

    decision: Decision
    notes: str = ""


class ConfirmationAdapter(Protocol):
    """Protocol for human-in-the-loop confirmation channels.

    Implementations might surface the memo via the console, Slack, WhatsApp,
    an internal web UI, etc. The orchestration layer doesn't care which
    channel — it only needs a Decision back.
    """

    def confirm(self, synthesis: SynthesisResult) -> GateResponse: ...


class ConsoleAdapter:
    """Default adapter: render memo to stdout, read decision from stdin.

    Useful for local dev and CI. In production you'd plug in a richer
    channel (Slack thread with reply buttons, web UI with diff view)
    but the Protocol is identical.
    """

    SEPARATOR = "─" * 70

    def confirm(self, synthesis: SynthesisResult) -> GateResponse:
        self._render(synthesis)
        return self._prompt()

    def _render(self, synthesis: SynthesisResult) -> None:
        memo = synthesis.memo
        company = synthesis.company

        print()
        print(self.SEPARATOR)
        print(f"CFO MEMO: {company.name} ({company.ticker})")
        print(
            f"Model: {synthesis.model} | "
            f"tokens: {synthesis.input_tokens} in, {synthesis.output_tokens} out"
        )
        print(self.SEPARATOR)
        print()
        print("EXECUTIVE SUMMARY")
        print(memo.executive_summary)
        print()
        print("FINANCIAL HIGHLIGHTS")
        for item in memo.financial_highlights:
            print(f"  • {item}")
        print()
        print("KEY RISKS")
        for item in memo.key_risks:
            print(f"  • {item}")
        print()
        print("RECOMMENDED ACTIONS")
        for item in memo.recommended_actions:
            print(f"  • {item}")
        print()
        print(self.SEPARATOR)

    def _prompt(self) -> GateResponse:
        while True:
            response = input("Approve / Reject / Revise (a/r/v): ").strip().lower()
            if response in ("a", "approve"):
                return GateResponse(decision=Decision.APPROVE)
            if response in ("r", "reject"):
                notes = input("Reason (optional): ").strip()
                return GateResponse(decision=Decision.REJECT, notes=notes)
            if response in ("v", "revise"):
                notes = input("Revision notes: ").strip()
                return GateResponse(decision=Decision.REVISE, notes=notes)
            print("  Invalid choice. Enter 'a', 'r', or 'v'.")