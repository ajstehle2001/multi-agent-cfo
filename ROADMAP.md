# Roadmap

This document tracks planned work for `multi-agent-cfo`. Each milestone is scoped to be independently shippable and demonstrable.

## v0.1-alpha — Scaffold ✅

- [x] Repo created with MIT license
- [x] README with architecture overview and design principles
- [x] Roadmap committed

## v0.2 — Working Reference Implementation ✅

Goal: someone can clone the repo, plug in a Claude API key, run `python -m multi_agent_cfo`, and see a CFO memo for a real public company appear in the console.

- [x] Project skeleton (`src/`, `tests/`, `clients/`, `prompts/`)
- [x] Intelligence Layer
  - [x] SIC code lookup module
  - [x] SEC EDGAR peer fetcher (with timeout + retry)
  - [x] Claude synthesis client (structured output)
- [x] Scheduler with `clients.yaml` config + asyncio loop
- [x] Confirmation Gate with console adapter (approve / reject / revise)
- [x] Three demo clients (Costco, Best Buy, Etsy)
- [x] Quick start documentation in README
- [x] Basic eval: output schema validation + LLM-as-judge sanity check

## v0.3 — Pluggability and Reliability

Goal: every external dependency is swappable, every failure mode has a defined behavior.

- [x] Adapter interface for output channels (console, file, stub WhatsApp, stub email) via OutputAdapter Protocol
- [x] Adapter interface for LLM client via Protocol (Claude default; OpenAI/Bedrock/local pluggable by implementing the LLM Protocol)
- [x] Structured logging with correlation IDs (JSONL to file + human-readable stderr, run_id and client ticker auto-propagated via contextvars)
- [x] Timeout + retry + circuit breaker on all external calls (per-host circuit breakers for SEC EDGAR and Anthropic API; tenacity retry inside breaker so transient blips don't consume the failure budget; retry filter narrowed to true transient errors only)
- [x] Failure-mode test matrix (23 tests: circuit breaker state machine, schema validation for CFOMemo and JudgeScores, scheduler integration covering synthesis failure, judge failure, output failure, reject/revise decisions, and resource cleanup)

## v1.0 — Production Maturity Signals

Goal: the repo demonstrates the engineering practices that distinguish production AI work from prototype AI work.

- [ ] **Confirmation Gate as an MCP server** — expose approval workflow via Model Context Protocol
- [ ] **LangGraph or Claude Agent SDK** for orchestration (replacing bespoke dispatch)
- [ ] **Versioned prompts** in `prompts/` with regression test suite
- [ ] **PII boundary documentation** — explicit data flow diagram showing what reaches the model and what doesn't
- [ ] CI/CD: prompt regression tests on every PR
- [ ] Architecture decision records (ADRs) for the key tradeoffs

## v2.0 and beyond — Stretch

Not committed, just where the project could go:

- Multi-tenant config (per-client prompts, models, output channels)
- LLM-as-judge eval framework expansion
- Real WhatsApp / Slack / email output adapters
- Web UI for the Confirmation Gate (instead of CLI)
- Drift detection on synthesis quality over time

---

*Last updated: June 2026. Status updates committed alongside feature work.*