"""Failure-mode integration tests for the scheduler.

Exercises run_scheduler with all-fake dependencies (FakeLLM, FakeEdgar,
ProgrammableConfirmAdapter, RecordingOutput) so the full pipeline runs
without network calls and with full control over which steps succeed.

Coverage focus is the failure-handling matrix:
  - Synthesis raises → recorded as failure, run continues with next client
  - Judge raises → non-fatal, decision still happens
  - Output adapter raises → decision counts, delivered=False
  - Reject / Revise decisions → output adapter not called
  - Edgar.close() runs even when iteration fails
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from multi_agent_cfo.gate.gate import Decision
from multi_agent_cfo.scheduler.scheduler import run_scheduler

from tests.doubles import (
    FakeEdgar,
    FakeLLM,
    ProgrammableConfirmAdapter,
    RecordingOutput,
    make_company,
    make_judge_json_response,
    make_memo_json_response,
)


@pytest.fixture
def clients_yaml(tmp_path: Path) -> Path:
    """Three-client YAML written to a per-test temporary directory."""
    yaml_path = tmp_path / "clients.yaml"
    data = {
        "clients": [
            {"ticker": "AAA", "name": "Alpha Co."},
            {"ticker": "BBB", "name": "Beta Co."},
            {"ticker": "CCC", "name": "Gamma Co."},
        ]
    }
    yaml_path.write_text(yaml.safe_dump(data))
    return yaml_path


def _three_client_edgar() -> FakeEdgar:
    return FakeEdgar(
        companies={
            "AAA": make_company(ticker="AAA", name="Alpha Co."),
            "BBB": make_company(ticker="BBB", name="Beta Co."),
            "CCC": make_company(ticker="CCC", name="Gamma Co."),
        }
    )


def test_happy_path_all_approved_all_delivered(clients_yaml: Path):
    """Three clients, all succeed end-to-end, all delivered."""
    edgar = _three_client_edgar()
    llm = FakeLLM(responses=[
        make_memo_json_response(),  # AAA synthesis
        make_judge_json_response(),  # AAA judge
        make_memo_json_response(),  # BBB synthesis
        make_judge_json_response(),  # BBB judge
        make_memo_json_response(),  # CCC synthesis
        make_judge_json_response(),  # CCC judge
    ])
    adapter = ProgrammableConfirmAdapter([Decision.APPROVE] * 3)
    output = RecordingOutput()

    summary = run_scheduler(
        clients_path=clients_yaml,
        adapter=adapter,
        output=output,
        edgar=edgar,
        llm=llm,
    )

    assert summary.count_by_decision(Decision.APPROVE) == 3
    assert summary.failures == 0
    assert summary.delivered_count == 3
    assert output.delivered == ["AAA", "BBB", "CCC"]
    assert edgar.closed is True


def test_synthesis_failure_continues_to_next_client(clients_yaml: Path):
    """Synthesis raising for one client should not stop subsequent clients."""
    edgar = _three_client_edgar()
    llm = FakeLLM(responses=[
        RuntimeError("AAA synthesis exploded"),  # AAA synthesis fails
        # No judge call for AAA since synthesis failed
        make_memo_json_response(),  # BBB synthesis
        make_judge_json_response(),  # BBB judge
        make_memo_json_response(),  # CCC synthesis
        make_judge_json_response(),  # CCC judge
    ])
    adapter = ProgrammableConfirmAdapter([Decision.APPROVE, Decision.APPROVE])
    output = RecordingOutput()

    summary = run_scheduler(
        clients_path=clients_yaml,
        adapter=adapter,
        output=output,
        edgar=edgar,
        llm=llm,
    )

    assert summary.failures == 1
    assert summary.count_by_decision(Decision.APPROVE) == 2
    assert summary.delivered_count == 2
    assert output.delivered == ["BBB", "CCC"]
    # AAA result should be marked unsuccessful with an error message
    aaa = next(r for r in summary.results if r.ticker == "AAA")
    assert aaa.success is False
    assert "exploded" in aaa.error


def test_judge_failure_is_non_fatal(clients_yaml: Path):
    """Judge raising should not block the confirmation gate or output."""
    edgar = _three_client_edgar()
    llm = FakeLLM(responses=[
        make_memo_json_response(),  # AAA synthesis
        RuntimeError("AAA judge exploded"),  # AAA judge fails
        make_memo_json_response(),  # BBB synthesis
        make_judge_json_response(),  # BBB judge
        make_memo_json_response(),  # CCC synthesis
        make_judge_json_response(),  # CCC judge
    ])
    adapter = ProgrammableConfirmAdapter([Decision.APPROVE] * 3)
    output = RecordingOutput()

    summary = run_scheduler(
        clients_path=clients_yaml,
        adapter=adapter,
        output=output,
        edgar=edgar,
        llm=llm,
    )

    assert summary.failures == 0
    assert summary.count_by_decision(Decision.APPROVE) == 3
    assert summary.delivered_count == 3
    # AAA should have no judge score but still be a successful run
    aaa = next(r for r in summary.results if r.ticker == "AAA")
    assert aaa.success is True
    assert aaa.judge_scores is None
    # BBB should have judge scores
    bbb = next(r for r in summary.results if r.ticker == "BBB")
    assert bbb.judge_scores is not None


def test_reject_and_revise_decisions_skip_output(clients_yaml: Path):
    """Output adapter should only be invoked for APPROVE decisions."""
    edgar = _three_client_edgar()
    llm = FakeLLM(responses=[
        make_memo_json_response(), make_judge_json_response(),
        make_memo_json_response(), make_judge_json_response(),
        make_memo_json_response(), make_judge_json_response(),
    ])
    adapter = ProgrammableConfirmAdapter([
        Decision.APPROVE,
        Decision.REJECT,
        Decision.REVISE,
    ])
    output = RecordingOutput()

    summary = run_scheduler(
        clients_path=clients_yaml,
        adapter=adapter,
        output=output,
        edgar=edgar,
        llm=llm,
    )

    assert summary.count_by_decision(Decision.APPROVE) == 1
    assert summary.count_by_decision(Decision.REJECT) == 1
    assert summary.count_by_decision(Decision.REVISE) == 1
    assert summary.delivered_count == 1
    assert output.delivered == ["AAA"]


def test_output_failure_is_non_fatal(clients_yaml: Path):
    """If the output adapter raises, the decision still counts as approved.

    Output is a side-effect, not part of the trust boundary. A delivery
    failure should be logged and observable via delivered_count, but it
    must not roll back the human's approve decision.
    """
    edgar = _three_client_edgar()
    llm = FakeLLM(responses=[
        make_memo_json_response(), make_judge_json_response(),
        make_memo_json_response(), make_judge_json_response(),
        make_memo_json_response(), make_judge_json_response(),
    ])
    adapter = ProgrammableConfirmAdapter([Decision.APPROVE] * 3)
    output = RecordingOutput(raise_on_deliver=True)

    summary = run_scheduler(
        clients_path=clients_yaml,
        adapter=adapter,
        output=output,
        edgar=edgar,
        llm=llm,
    )

    assert summary.failures == 0
    assert summary.count_by_decision(Decision.APPROVE) == 3
    assert summary.delivered_count == 0
    assert output.delivered == []
    # Per-client result should reflect not-delivered
    for r in summary.results:
        assert r.success is True
        assert r.decision == Decision.APPROVE
        assert r.delivered is False


def test_edgar_close_called_even_when_all_clients_fail(clients_yaml: Path):
    """The edgar.close() cleanup must run even when every iteration raises."""
    edgar = _three_client_edgar()
    llm = FakeLLM(responses=[
        RuntimeError("AAA synthesis failed"),
        RuntimeError("BBB synthesis failed"),
        RuntimeError("CCC synthesis failed"),
    ])
    adapter = ProgrammableConfirmAdapter([])  # should never be invoked
    output = RecordingOutput()

    summary = run_scheduler(
        clients_path=clients_yaml,
        adapter=adapter,
        output=output,
        edgar=edgar,
        llm=llm,
    )

    assert summary.failures == 3
    assert summary.count_by_decision(Decision.APPROVE) == 0
    assert summary.delivered_count == 0
    assert edgar.closed is True
    assert adapter.calls == []  # gate never reached