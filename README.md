# multi-agent-cfo

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)
[![Status](https://img.shields.io/badge/status-v0.2-green.svg)](#project-status)

> A multi-agent platform that generates monthly CFO-style memos for a portfolio of companies — peer-benchmarked against SEC EDGAR data, synthesized by Claude, and gated by human approval before delivery.

## Why this exists

Most LLM agent demos are either toys (chatbots) or black boxes (single-agent monoliths). This project is a reference implementation of a different pattern: a **production-shaped, confirmation-gated, multi-agent workflow** that does real work against real data — and is structured so the human stays in control.

The reference use case is a fractional-CFO scenario: every month, generate a financial intelligence memo for each portfolio company, benchmarked against industry peers, and surface it to a human for approval before it goes anywhere.

But the architecture is the point, not the use case. The same skeleton would serve any periodic, agent-driven, human-confirmed workflow: compliance reviews, research briefings, M&A monitoring, ops health checks.

## Architecture

```mermaid
flowchart LR
    S[Scheduler<br/>monthly trigger] --> IL[Intelligence Layer]
    IL --> SIC[SIC Code Lookup]
    IL --> EDGAR[SEC EDGAR<br/>Peer Benchmarker]
    IL --> Claude[Claude API<br/>Memo Synthesis]
    Claude --> CG[Confirmation Gate]
    CG -->|approved| OA[Output Adapter]
    CG -->|rejected| Log[Audit Log]
    OA --> Out[Console / WhatsApp / Email]
```

Three core components, each independently testable and replaceable:

### Intelligence Layer
For a given company ticker, fetches the SIC code, identifies industry peers via SEC EDGAR, retrieves their latest 10-K/10-Q filings, and produces a structured peer-benchmarked dataset. Passes that to Claude with a synthesis prompt that yields a CFO-style memo.

### Scheduler
Config-driven monthly trigger. Reads a `clients.yaml` defining which companies to analyze and on what cadence. Invokes the Intelligence Layer for each client. Intentionally lightweight — async loop, not a Kubernetes job.

### Confirmation Gate
The trust boundary. Before any memo is delivered, it's surfaced to a human reviewer via a pluggable adapter (default: console; reference WhatsApp adapter stub included). The human approves, rejects, or requests revision. Nothing ships without approval.

## Quick start

### Prerequisites

- Python 3.11 or higher
- An Anthropic API key — [create one in the Anthropic console](https://console.anthropic.com/settings/keys)

### Installation

```bash
# Clone the repo
git clone https://github.com/ajstehle2001/multi-agent-cfo.git
cd multi-agent-cfo

# Create and activate a virtual environment
python -m venv venv

# Activate (pick the line for your shell)
source venv/bin/activate         # Linux / macOS
.\venv\Scripts\Activate.ps1      # Windows PowerShell

# Install the project + dev dependencies in editable mode
pip install -e ".[dev]"
```

### Configuration

Copy the example environment file and add your API key:

```bash
cp .env.example .env             # Linux / macOS
copy .env.example .env           # Windows
```

Open `.env` and replace the placeholder with your real Anthropic API key. The `.env` file is gitignored — your key stays local.

### Run

```bash
python -m multi_agent_cfo
```

What happens:

1. The scheduler loads the demo client list from `clients/clients.yaml` (Costco, Best Buy, Etsy by default).
2. For each client: SEC EDGAR lookup → Claude synthesis → schema validation against the `CFOMemo` Pydantic model.
3. Each memo is rendered in the console and you're prompted to **a**pprove, **r**eject, or **revise**.
4. A run summary prints at the end with per-decision counts and total token usage.

Cost is approximately $0.01–0.03 per memo at current Claude Sonnet pricing.

### Customizing the client list

Edit `clients/clients.yaml` with any public US company ticker that appears in SEC EDGAR's registry:

```yaml
clients:
  - ticker: AAPL
    name: Apple Inc.
  - ticker: NVDA
    name: NVIDIA Corporation
  - ticker: SBUX
    name: Starbucks Corporation
```

Re-run `python -m multi_agent_cfo` and the new client list takes effect.

## Project status

**v0.2** — end-to-end pipeline complete. SEC EDGAR data, Claude synthesis, schema-validated outputs, LLM-as-judge quality scoring, and human-in-the-loop confirmation. Runnable with `python -m multi_agent_cfo`. Active development continues toward v0.3 (see [ROADMAP.md](ROADMAP.md)).

See [ROADMAP.md](ROADMAP.md) for the milestone plan.

## Design principles

These are the choices that distinguish this from a typical agent demo:

- **Failure-first design.** Every external dependency (Claude API, SEC EDGAR, the messaging adapter) is wrapped in timeout + retry + fallback logic. Failures are logged with enough context to diagnose post-hoc.
- **Pluggable adapters everywhere.** LLM client, output channel, and data sources are all interfaces. You can swap Claude for any model, WhatsApp for Slack, EDGAR for any data source — without touching the orchestration layer.
- **Human-in-the-loop by default.** No memo is ever delivered without explicit human approval. The confirmation gate is not an afterthought; it's the central design assumption.
- **Public data only.** This reference implementation operates exclusively on publicly available SEC filings. The PII / sensitive-data boundary is enforced at the data-ingestion layer, before model input — not after.
- **Eval-driven.** Synthesis outputs are validated against a schema and sampled for LLM-as-judge sanity checking. Quality is measured, not assumed.

## License

MIT — see [LICENSE](LICENSE).
