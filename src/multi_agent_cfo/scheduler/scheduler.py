"""Scheduler: orchestrate memo generation for all configured clients.

Reads client configuration from clients/clients.yaml, runs the full
Intelligence Layer -> LLM-as-judge -> Confirmation Gate -> Output pipeline
for each client, and aggregates results into a run summary.

Single-client failures are logged but do not terminate the run.
Output failures are also non-fatal -- the decision is still recorded;
only the delivery side-effect is lost.

All operational events flow through the structured logger and carry
run_id + client correlation IDs (see observability.logging_config).
Interactive UI (memo display, approve/reject prompt, final run summary
banner) stays on stdout via print().

The edgar and llm parameters of run_scheduler are dependency-injection
seams: tests pass fakes that satisfy the same interfaces without making
real network calls.
"""

from __future__ import annotations

import logging
import uuid
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
from multi_agent_cfo.intelligence.llm import LLM, LLMClient
from multi_agent_cfo.intelligence.synthesis import synthesize_memo
from multi_agent_cfo.observability.logging_config import (
    client_context,
    configure_logging,
    run_context,
)
from multi_agent_cfo.output.output import ConsoleOutputAdapter, OutputAdapter


DEFAULT_CLIENTS_PATH = Path("clients/clients.yaml")

logger = logging.getLogger(__name__)


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
    run_id: str = ""

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
    edgar: EdgarClient | None = None,
    llm: LLM | None = None,
) -> RunSummary:
    """Run synthesis + judge + confirmation + output for every client."""
    log_path = configure_logging()
    run_id = uuid.uuid4().hex[:12]

    with run_context(run_id):
        logger.info("Scheduler starting (log_file=%s)", log_path)

        adapter = adapter or ConsoleAdapter()
        output = output or ConsoleOutputAdapter()
        clients = load_clients(clients_path)
        summary = RunSummary(run_id=run_id)

        logger.info(
            "Loaded %d clients from %s", len(clients), clients_path,
            extra={"client_count": len(clients)},
        )

        edgar = edgar or EdgarClient()
        llm = llm or LLMClient()

        try:
            for idx, client in enumerate(clients, start=1):
                with client_context(client.ticker):
                    label = client.name or "unknown"
                    logger.info(
                        "Processing %s (%s) [%d/%d]",
                        client.ticker, label, idx, len(clients),
                    )

                    try:
                        synthesis = synthesize_memo(client.ticker, edgar=edgar, llm=llm)
                        logger.info(
                            "Synthesis ok (tokens_in=%d tokens_out=%d)",
                            synthesis.input_tokens, synthesis.output_tokens,
                            extra={
                                "stage": "synthesis",
                                "tokens_in": synthesis.input_tokens,
                                "tokens_out": synthesis.output_tokens,
                            },
                        )
                    except Exception as exc:
                        logger.error(
                            "Synthesis failed: %s", exc,
                            exc_info=True,
                            extra={"stage": "synthesis"},
                        )
                        summary.results.append(
                            ClientRunResult(
                                ticker=client.ticker,
                                success=False,
                                error=str(exc),
                            )
                        )
                        continue

                    try:
                        judgment = judge_memo(synthesis, llm=llm)
                        pass_label = "PASS" if judgment.scores.passes else "FAIL"
                        logger.info(
                            "Judge: spec=%d ground=%d action=%d num=%d mean=%.2f %s",
                            judgment.scores.specificity,
                            judgment.scores.grounding,
                            judgment.scores.actionability,
                            judgment.scores.numeric_honesty,
                            judgment.scores.mean,
                            pass_label,
                            extra={
                                "stage": "judge",
                                "judge_pass": judgment.scores.passes,
                                "judge_mean": judgment.scores.mean,
                                "judge_specificity": judgment.scores.specificity,
                                "judge_grounding": judgment.scores.grounding,
                                "judge_actionability": judgment.scores.actionability,
                                "judge_numeric_honesty": judgment.scores.numeric_honesty,
                            },
                        )
                    except Exception as exc:
                        logger.warning(
                            "Judge failed (continuing): %s", exc,
                            extra={"stage": "judge"},
                        )
                        judgment = None

                    response = adapter.confirm(synthesis)
                    logger.info(
                        "Decision: %s", response.decision.value,
                        extra={"stage": "gate", "decision": response.decision.value},
                    )

                    delivered = False
                    if response.decision == Decision.APPROVE:
                        try:
                            output.deliver(synthesis)
                            delivered = True
                            logger.info(
                                "Output delivered",
                                extra={"stage": "output"},
                            )
                        except Exception as exc:
                            logger.warning(
                                "Output delivery failed: %s", exc,
                                exc_info=True,
                                extra={"stage": "output"},
                            )

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

        finally:
            edgar.close()

        logger.info(
            "Run complete: processed=%d approved=%d rejected=%d revised=%d "
            "failed=%d delivered=%d",
            len(summary.results),
            summary.count_by_decision(Decision.APPROVE),
            summary.count_by_decision(Decision.REJECT),
            summary.count_by_decision(Decision.REVISE),
            summary.failures,
            summary.delivered_count,
            extra={
                "stage": "run_complete",
                "processed": len(summary.results),
                "approved": summary.count_by_decision(Decision.APPROVE),
                "rejected": summary.count_by_decision(Decision.REJECT),
                "revised": summary.count_by_decision(Decision.REVISE),
                "failed": summary.failures,
                "delivered": summary.delivered_count,
                "synthesis_tokens_in": summary.total_input_tokens,
                "synthesis_tokens_out": summary.total_output_tokens,
                "judge_tokens_in": summary.total_judge_input_tokens,
                "judge_tokens_out": summary.total_judge_output_tokens,
            },
        )

    print()
    print("=" * 70)
    print(f"RUN SUMMARY  (run_id={run_id})")
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
        mean_str = (
            f"{summary.mean_judge_score:.2f}"
            if summary.mean_judge_score
            else "n/a"
        )
        print(
            f"Judge tokens:     {summary.total_judge_input_tokens} in, "
            f"{summary.total_judge_output_tokens} out"
        )
        print(
            f"Judge pass rate:  {summary.judge_pass_count}/{summary.judged_count} "
            f"(mean score {mean_str})"
        )
    print(f"Log file: {log_path}")
    print()

    return summary
