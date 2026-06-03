"""Scheduler: orchestrate memo generation for all configured clients.

Reads client configuration from clients/clients.yaml, runs the full
Intelligence Layer → Confirmation Gate pipeline for each client, and
aggregates results into a run summary.

Single-client failures are logged but do not terminate the run — the
multi-client setting demands resilience to individual failures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from multi_agent_cfo.gate.gate import (
    ConfirmationAdapter,
    ConsoleAdapter,
    Decision,
)
from multi_agent_cfo.intelligence.edgar import EdgarClient
from multi_agent_cfo.intelligence.llm import LLMClient
from multi_agent_cfo.intelligence.synthesis import synthesize_memo


DEFAULT_CLIENTS_PATH = Path("clients/clients.yaml")


@dataclass(frozen=True)
class ClientConfig:
    """Configuration for a single client in the synthesis run."""

    ticker: str
    name: str = ""


@dataclass
class ClientRunResult:
    """Outcome of attempting one client's pipeline."""

    ticker: str
    success: bool
    decision: Decision | None = None
    notes: str = ""
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class RunSummary:
    """Aggregate results of a full scheduler run."""

    results: list[ClientRunResult] = field(default_factory=list)

    @property
    def total_input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.results)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.results)

    def count_by_decision(self, decision: Decision) -> int:
        return sum(1 for r in self.results if r.decision == decision)

    @property
    def failures(self) -> int:
        return sum(1 for r in self.results if not r.success)


def load_clients(path: Path = DEFAULT_CLIENTS_PATH) -> list[ClientConfig]:
    """Load client configurations from a YAML file."""
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return [
        ClientConfig(ticker=entry["ticker"], name=entry.get("name", ""))
        for entry in raw.get("clients", [])
    ]


def run_scheduler(
    clients_path: Path = DEFAULT_CLIENTS_PATH,
    adapter: ConfirmationAdapter | None = None,
) -> RunSummary:
    """Run synthesis + confirmation for every configured client.

    Single-client failures are caught and recorded; they don't terminate
    the run. The aggregate RunSummary is printed at the end and returned.
    """
    adapter = adapter or ConsoleAdapter()
    clients = load_clients(clients_path)
    summary = RunSummary()

    print("multi-agent-cfo v0.2-alpha")
    print(f"Loaded {len(clients)} clients from {clients_path}")
    print()

    # Share EDGAR and LLM client instances across the run for connection reuse
    # and to take advantage of EdgarClient's cached ticker registry.
    edgar = EdgarClient()
    llm = LLMClient()

    try:
        for idx, client in enumerate(clients, start=1):
            label = client.name or "unknown"
            print(f"[{idx}/{len(clients)}] Processing {client.ticker} ({label})")

            try:
                synthesis = synthesize_memo(client.ticker, edgar=edgar, llm=llm)
            except Exception as exc:
                print(f"  ✗ Synthesis failed: {exc}")
                summary.results.append(
                    ClientRunResult(
                        ticker=client.ticker,
                        success=False,
                        error=str(exc),
                    )
                )
                continue

            response = adapter.confirm(synthesis)
            summary.results.append(
                ClientRunResult(
                    ticker=client.ticker,
                    success=True,
                    decision=response.decision,
                    notes=response.notes,
                    input_tokens=synthesis.input_tokens,
                    output_tokens=synthesis.output_tokens,
                )
            )
            print(f"  Decision: {response.decision.value}")
            print()

    finally:
        edgar.close()

    # Print summary
    print("=" * 70)
    print("RUN SUMMARY")
    print("=" * 70)
    print(f"Processed: {len(summary.results)}")
    print(f"  Approved: {summary.count_by_decision(Decision.APPROVE)}")
    print(f"  Rejected: {summary.count_by_decision(Decision.REJECT)}")
    print(f"  Revised:  {summary.count_by_decision(Decision.REVISE)}")
    print(f"  Failed:   {summary.failures}")
    print(f"Total tokens: {summary.total_input_tokens} in, {summary.total_output_tokens} out")
    print()

    return summary