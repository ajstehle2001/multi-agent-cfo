"""Scheduler: orchestrate memo generation for all configured clients.

Reads client configuration from clients/clients.yaml, runs the full
Intelligence Layer → LLM-as-judge → Confirmation Gate → Output pipeline
for each client, and aggregates results into a run summary.

Single-client failures are logged but do not terminate the run.
Output failures are also non-fatal — the decision is still recorded;
only the delivery side-effect is lost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from multi_agent_cfo.evals.judge import JudgeScores, judge_memo
from multi_agent_cfo.gate.gate import (
    ConfirmationAdapter,
    ConsoleAdapter,
    Decision,
)
from multi_agent_cfo.intelligence.edgar import EdgarClient
from multi_agent_cfo.intelligence.llm import LLMClient
from multi_agent_cfo.intelligence.synthesis import synthesize_memo
from multi_agent_cfo.output.output import ConsoleOutputAdapter, OutputAdapter


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
    judge_scores: JudgeScores | None = None
    judge_input_tokens: int = 0
    judge_output_tokens: int = 0
    delivered: bool = False


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

    @property
    def total_judge_input_tokens(self) -> int:
        return sum(r.judge_input_tokens for r in self.results)

    @property
    def total_judge_output_tokens(self) -> int:
        return sum(r.judge_output_tokens for r in self.results)

    @property
    def judged_count(self) -> int:
        return sum(1 for r in self.results if r.judge_scores is not None)

    @property
    def judge_pass_count(self) -> int:
        return sum(1 for r in self.results if r.judge_scores and r.judge_scores.passes)

    @property
    def mean_judge_score(self) -> float | None:
        scored = [r.judge_scores.mean for r in self.results if r.judge_scores]
        return sum(scored) / len(scored) if scored else None

    @property
    def delivered_count(self) -> int:
        return sum(1 for r in self.results if r.delivered)

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
    output: OutputAdapter | None = None,
) -> RunSummary:
    """Run synthesis + judge + confirmation + output for every client.

    Pipeline per client:
      1. SEC EDGAR lookup + Claude synthesis (synthesize_memo)
      2. LLM-as-judge quality scoring (judge_memo) — non-fatal on failure
      3. Human-in-the-loop confirmation (adapter.confirm)
      4. Output delivery for approved memos only (output.deliver)

    Single-client failures are caught and recorded; they don't terminate
    the run. Output failures are also non-fatal — the decision still
    counts, only the delivery side-effect is lost.
    """
    adapter = adapter or ConsoleAdapter()
    output = output or ConsoleOutputAdapter()
    clients = load_clients(clients_path)
    summary = RunSummary()

    print("multi-agent-cfo v0.2")
    print(f"Loaded {len(clients)} clients from {clients_path}")
    print()

    edgar = EdgarClient()
    llm = LLMClient()

    try:
        for idx, client in enumerate(clients, start=1):
            label = client.name or "unknown"
            print(f"[{idx}/{len(clients)}] Processing {client.ticker} ({label})")

            # 1. Synthesize the memo
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

            # 2. Judge the memo
            try:
                judgment = judge_memo(synthesis, llm=llm)
                pass_label = "PASS" if judgment.scores.passes else "FAIL"
                print(
                    f"  Judge: spec={judgment.scores.specificity} "
                    f"ground={judgment.scores.grounding} "
                    f"action={judgment.scores.actionability} "
                    f"num={judgment.scores.numeric_honesty} "
                    f"(mean={judgment.scores.mean:.2f}, {pass_label})"
                )
            except Exception as exc:
                print(f"  ⚠ Judge failed (continuing): {exc}")
                judgment = None

            # 3. Human confirmation gate
            response = adapter.confirm(synthesis)
            print(f"  Decision: {response.decision.value}")

            # 4. Output delivery for approved memos only
            delivered = False
            if response.decision == Decision.APPROVE:
                try:
                    output.deliver(synthesis)
                    delivered = True
                except Exception as exc:
                    print(f"  ⚠ Output delivery failed: {exc}")

            summary.results.append(
                ClientRunResult(
                    ticker=client.ticker,
                    success=True,
                    decision=response.decision,
                    notes=response.notes,
                    input_tokens=synthesis.input_tokens,
                    output_tokens=synthesis.output_tokens,
                    judge_scores=judgment.scores if judgment else None,
                    judge_input_tokens=judgment.judge_input_tokens if judgment else 0,
                    judge_output_tokens=judgment.judge_output_tokens if judgment else 0,
                    delivered=delivered,
                )
            )
            print()

    finally:
        edgar.close()

    print("=" * 70)
    print("RUN SUMMARY")
    print("=" * 70)
    print(f"Processed: {len(summary.results)}")
    print(f"  Approved: {summary.count_by_decision(Decision.APPROVE)}")
    print(f"  Rejected: {summary.count_by_decision(Decision.REJECT)}")
    print(f"  Revised:  {summary.count_by_decision(Decision.REVISE)}")
    print(f"  Failed:   {summary.failures}")
    print(f"  Delivered: {summary.delivered_count}")
    print(
        f"Synthesis tokens: {summary.total_input_tokens} in, "
        f"{summary.total_output_tokens} out"
    )
    if summary.judged_count > 0:
        mean_str = f"{summary.mean_judge_score:.2f}" if summary.mean_judge_score else "n/a"
        print(
            f"Judge tokens:     {summary.total_judge_input_tokens} in, "
            f"{summary.total_judge_output_tokens} out"
        )
        print(
            f"Judge pass rate:  {summary.judge_pass_count}/{summary.judged_count} "
            f"(mean score {mean_str})"
        )
    print()

    return summary