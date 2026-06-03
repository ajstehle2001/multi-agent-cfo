"""LLM-as-judge evaluation for synthesized CFO memos.

Uses an LLM to score memo quality along four dimensions:
- specificity: does the memo cite concrete, company-specific facts?
- grounding: are claims plausible given what is publicly known?
- actionability: are recommendations specific enough to act on?
- numeric_honesty: does the memo avoid fabricating precise financial figures?

This is NOT a replacement for human review — it's a fast, cheap signal
that catches obvious quality regressions during development and provides
aggregate quality metrics over time. The Confirmation Gate (human review)
remains the trust boundary for delivery.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, Field

from multi_agent_cfo.intelligence.llm import LLMClient
from multi_agent_cfo.intelligence.synthesis import SynthesisResult


JUDGE_SYSTEM_PROMPT = """You are an evaluator scoring a CFO memo's quality. You will rate it on four dimensions, each 1-5:

- specificity: Does the memo cite concrete, company-specific facts? (5 = many specific details about THIS company's business model, strategy, products; 1 = could apply to any company in any industry)
- grounding: Are the claims plausible given what is publicly known about the company? (5 = all claims align with widely reported facts; 1 = contains clearly wrong or fabricated claims)
- actionability: Are the recommended actions specific enough to act on? (5 = each recommendation has a clear owner and a measurable outcome; 1 = vague platitudes like "consider strategy")
- numeric_honesty: Does the memo avoid fabricating precise financial figures? (5 = no specific dollar amounts or percentages presented as if from filings; 1 = fabricated specific numbers presented as fact)

Your output must be VALID JSON matching this schema exactly:
{
  "specificity": <int 1-5>,
  "grounding": <int 1-5>,
  "actionability": <int 1-5>,
  "numeric_honesty": <int 1-5>,
  "rationale": "<2-3 sentences explaining the most material observations behind the scores>"
}

Rules:
- Output ONLY the JSON object. No prose before or after, no markdown fences.
- Be calibrated: a 5 means excellent on that dimension, not "no complaints". A 3 is acceptable but unremarkable.
- Score conservatively. Most memos that pass schema validation are 3s and 4s; reserve 5s for truly excellent work.
"""


class JudgeScores(BaseModel):
    """Quality scores for a CFO memo, 1-5 scale per dimension."""

    specificity: int = Field(..., ge=1, le=5)
    grounding: int = Field(..., ge=1, le=5)
    actionability: int = Field(..., ge=1, le=5)
    numeric_honesty: int = Field(..., ge=1, le=5)
    rationale: str = Field(..., min_length=20)

    @property
    def mean(self) -> float:
        return (
            self.specificity + self.grounding + self.actionability + self.numeric_honesty
        ) / 4

    @property
    def passes(self) -> bool:
        """Conservative pass bar: every dimension >= 3 AND mean >= 3.5."""
        return (
            self.specificity >= 3
            and self.grounding >= 3
            and self.actionability >= 3
            and self.numeric_honesty >= 3
            and self.mean >= 3.5
        )


@dataclass(frozen=True)
class JudgeResult:
    """Outcome of judging a synthesis result.

    Provenance metadata (model, tokens) is preserved for audit trails
    and cost accounting alongside the actual scores.
    """

    scores: JudgeScores
    judge_model: str
    judge_input_tokens: int
    judge_output_tokens: int


def judge_memo(
    synthesis: SynthesisResult,
    *,
    llm: LLMClient | None = None,
) -> JudgeResult:
    """Score a synthesized memo using an LLM-as-judge.

    For v0.2 the judge uses the same model as the synthesizer. In future
    versions a different model would give a slightly stronger signal
    (less correlation between generator and judge), but the same-model
    setup is sufficient for catching obvious regressions.
    """
    llm = llm or LLMClient()

    user_prompt = f"""Evaluate this CFO memo.

Company: {synthesis.company.name} ({synthesis.company.ticker})
Industry (SIC): {synthesis.company.sic_description}

MEMO TO EVALUATE:
{synthesis.memo.model_dump_json(indent=2)}

Output your scoring JSON now."""

    response = llm.complete(
        system=JUDGE_SYSTEM_PROMPT,
        user=user_prompt,
    )

    # Defensively strip markdown code fences in case the model wraps JSON.
    text = response.text.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").strip()
        text = text.removesuffix("```").strip()

    scores_data = json.loads(text)
    scores = JudgeScores.model_validate(scores_data)

    return JudgeResult(
        scores=scores,
        judge_model=response.model,
        judge_input_tokens=response.input_tokens,
        judge_output_tokens=response.output_tokens,
    )