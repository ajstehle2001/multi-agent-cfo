"""Schema validation tests for CFOMemo and JudgeScores.

These guard the trust boundary between LLM output and downstream code.
A malformed or out-of-range LLM response must be rejected at the schema
layer rather than propagating to the Confirmation Gate or output adapters.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from multi_agent_cfo.evals.judge import JudgeScores
from multi_agent_cfo.intelligence.synthesis import CFOMemo


# ---- CFOMemo ---------------------------------------------------------------


def test_cfo_memo_accepts_minimum_valid_input():
    memo = CFOMemo(
        executive_summary="A reasonably long executive summary line.",
        financial_highlights=["one bullet"],
        key_risks=["one bullet"],
        recommended_actions=["one bullet"],
    )
    assert memo.executive_summary.startswith("A reasonably")


def test_cfo_memo_rejects_missing_required_field():
    with pytest.raises(ValidationError):
        CFOMemo(
            executive_summary="A reasonably long executive summary line.",
            financial_highlights=["one"],
            key_risks=["one"],
            # missing recommended_actions
        )


def test_cfo_memo_rejects_executive_summary_under_min_length():
    """Executive summary < 20 chars should fail schema validation."""
    with pytest.raises(ValidationError):
        CFOMemo(
            executive_summary="too short",
            financial_highlights=["one"],
            key_risks=["one"],
            recommended_actions=["one"],
        )


def test_cfo_memo_rejects_empty_list_field():
    """Empty list for any bullet field should fail schema validation."""
    with pytest.raises(ValidationError):
        CFOMemo(
            executive_summary="A reasonably long executive summary line.",
            financial_highlights=[],
            key_risks=["one"],
            recommended_actions=["one"],
        )


# ---- JudgeScores -----------------------------------------------------------


def test_judge_scores_accepts_min_and_max_values():
    s = JudgeScores(
        specificity=1,
        grounding=5,
        actionability=3,
        numeric_honesty=4,
        rationale="A reasonable explanation of the scoring rationale.",
    )
    assert s.mean == (1 + 5 + 3 + 4) / 4


def test_judge_scores_rejects_above_max():
    with pytest.raises(ValidationError):
        JudgeScores(
            specificity=6,
            grounding=3,
            actionability=3,
            numeric_honesty=3,
            rationale="A reasonable explanation of the scoring rationale.",
        )


def test_judge_scores_rejects_below_min():
    with pytest.raises(ValidationError):
        JudgeScores(
            specificity=0,
            grounding=3,
            actionability=3,
            numeric_honesty=3,
            rationale="A reasonable explanation of the scoring rationale.",
        )


def test_judge_scores_passes_threshold_mean_gte_3_5_all_dimensions_gte_3():
    s = JudgeScores(
        specificity=4,
        grounding=4,
        actionability=3,
        numeric_honesty=4,
        rationale="A reasonable explanation of the scoring rationale.",
    )
    assert s.mean == 3.75
    assert s.passes is True


def test_judge_scores_fails_when_mean_below_3_5():
    """All dims at minimum acceptable (3) gives mean = 3.0 which is below the 3.5 bar."""
    s = JudgeScores(
        specificity=3,
        grounding=3,
        actionability=3,
        numeric_honesty=3,
        rationale="A reasonable explanation of the scoring rationale.",
    )
    assert s.mean == 3.0
    assert s.passes is False


def test_judge_scores_fails_when_any_dimension_below_3():
    """Even a high mean fails if a single dimension drops below 3."""
    s = JudgeScores(
        specificity=2,
        grounding=5,
        actionability=5,
        numeric_honesty=5,
        rationale="A reasonable explanation of the scoring rationale.",
    )
    assert s.mean == 4.25
    assert s.passes is False