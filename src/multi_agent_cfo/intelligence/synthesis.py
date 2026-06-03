"""CFO memo synthesis: tie EDGAR data and Claude generation into one pipeline.

Takes a public company ticker, fetches company metadata from SEC EDGAR,
and asks Claude to produce a structured CFO-style memo. The output is
validated against a Pydantic schema so downstream consumers (Confirmation
Gate, output adapters, eval framework) get a guaranteed structure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, Field

from multi_agent_cfo.intelligence.edgar import CompanyInfo, EdgarClient
from multi_agent_cfo.intelligence.llm import LLMClient


SYNTHESIS_SYSTEM_PROMPT = """You are a fractional CFO producing a concise monthly intelligence memo for the board of directors of a portfolio company.

Your output must be VALID JSON matching this schema exactly:
{
  "executive_summary": "2-3 sentence overview of the company's current strategic position",
  "financial_highlights": ["3-5 short bullets covering the most material financial observations"],
  "key_risks": ["3-5 short bullets covering the most material risks to monitor"],
  "recommended_actions": ["2-4 short bullets recommending specific actions or areas to investigate further"]
}

Rules:
- Output ONLY the JSON object. No prose before or after, no markdown fences.
- Be specific and grounded in what is publicly known about the company and its industry.
- Avoid generic platitudes ("focus on innovation") — every bullet should be substantive.
- If you don't have specific financial figures, frame observations as industry-typical rather than fabricating numbers.
"""


SYNTHESIS_USER_TEMPLATE = """Generate a CFO memo for the following company:

Company: {name}
Ticker: {ticker}
SEC CIK: {cik}
SIC industry: {sic} ({sic_description})

Output the JSON memo now."""


class CFOMemo(BaseModel):
    """Structured CFO memo output.

    Pydantic validation ensures all downstream consumers get a consistent
    schema instead of re-parsing free-form LLM text.
    """

    executive_summary: str = Field(..., min_length=20)
    financial_highlights: list[str] = Field(..., min_length=1)
    key_risks: list[str] = Field(..., min_length=1)
    recommended_actions: list[str] = Field(..., min_length=1)


@dataclass(frozen=True)
class SynthesisResult:
    """A memo with its provenance metadata.

    Provenance matters: for LLM-as-judge evaluation and audit trails,
    we need to know which company data and which model produced each memo.
    """

    company: CompanyInfo
    memo: CFOMemo
    model: str
    input_tokens: int
    output_tokens: int


def synthesize_memo(
    ticker: str,
    *,
    edgar: EdgarClient | None = None,
    llm: LLMClient | None = None,
) -> SynthesisResult:
    """Generate a CFO memo for the given ticker.

    Composes the Intelligence Layer: EDGAR lookup → Claude synthesis →
    schema validation. Optional clients allow dependency injection for tests.
    """
    edgar = edgar or EdgarClient()
    llm = llm or LLMClient()

    company = edgar.lookup_company(ticker)

    user_prompt = SYNTHESIS_USER_TEMPLATE.format(
        name=company.name,
        ticker=company.ticker,
        cik=company.cik,
        sic=company.sic,
        sic_description=company.sic_description,
    )

    response = llm.complete(
        system=SYNTHESIS_SYSTEM_PROMPT,
        user=user_prompt,
    )

    # Defensively strip markdown code fences in case the model wraps JSON
    # despite the explicit "no fences" instruction.
    text = response.text.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").strip()
        text = text.removesuffix("```").strip()

    memo_data = json.loads(text)
    memo = CFOMemo.model_validate(memo_data)

    return SynthesisResult(
        company=company,
        memo=memo,
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )