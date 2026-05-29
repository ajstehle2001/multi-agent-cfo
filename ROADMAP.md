# Roadmap

This document tracks planned work for `multi-agent-cfo`. Each milestone is scoped to be independently shippable and demonstrable.

## v0.1-alpha — Scaffold ✅

- [x] Repo created with MIT license
- [x] README with architecture overview and design principles
- [x] Roadmap committed

## v0.2 — Working Reference Implementation

Goal: someone can clone the repo, plug in a Claude API key, run `python -m multi_agent_cfo`, and see a CFO memo for a real public company appear in the console.

- [ ] Project skeleton (`src/`, `tests/`, `clients/`, `prompts/`)
- [ ] Intelligence Layer
  - [ ] SIC code lookup module
  - [ ] SEC EDGAR peer fetcher (with timeout + retry)
  - [ ] Claude synthesis client (structured output)
- [ ] Scheduler with `clients.yaml` config + asyncio loop
- [ ] Confirmation Gate with console adapter (approve / reject / revise)
- [ ] Three demo clients (Costco, Best Buy, Etsy)
- [ ] Quick start documentation in README
- [ ] Basic eval: output schema validation + one LLM-as-judge check

## v0.3 — Pluggability and Reliability

Goal: every external dependency is swappable, every failure mode has a defined behavior.

- [ ] Adapter interface for output channels (console, file, stub WhatsApp, stub email)
- [ ] Adapter interface for LLM client (Claude default, OpenAI-compatible)
- [ ] Structured logging with correlation IDs
- [ ] Timeout + retry + circuit breaker on all external calls
- [ ] Failure-mode test matrix (network failures, malformed responses, missing data)

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

*Last updated: May 2026. Status updates committed alongside feature work.*
