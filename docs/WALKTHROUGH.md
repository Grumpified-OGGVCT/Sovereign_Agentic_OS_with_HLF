# 🚀 Sovereign Agentic OS — Getting Started Walkthrough

This guide walks you through forking, setting up, and running the Sovereign Agentic OS with HLF (Hieroglyphic Logic Framework). By the end, you'll have your own agent swarm processing translations with zero-trust governance.

---

## What You'll Get

| Feature | Description |
|---------|-------------|
| **HLF Translation Pipeline** | NLP → HLF → ALIGN → Agent → HLF → NLP with 60-67% token compression |
| **Live Dashboard** | GitHub Pages demo with commits, PRs, CI status, Jules cadence |
| **Agent Swarm (Jules)** | Autonomous daily/weekly/monthly maintenance and evolution |
| **Copilot Factory** | CLI to dispatch task-specific Copilot agents |
| **Unlimited Translations** | No 5/day quota on your own fork |

---

## Step 1 — Fork & Clone

```bash
# Fork the repo to your own account
gh repo fork Grumpified-OGGVCT/Sovereign_Agentic_OS_with_HLF --clone

# Enter the directory
cd Sovereign_Agentic_OS_with_HLF

# Install dependencies (uses uv — the lightning-fast Python package manager)
uv sync
```

> **Tip:** If you don't have `uv` installed: `pip install uv`

---

## Step 2 — Configure Environment

```bash
# Copy the env template
cp .env.example .env
```

### Required Keys

Edit your `.env` file with these values:

```env
# Redis password (gateway message bus)
REDIS_PASSWORD=your_redis_password

# Cloud model access via OpenRouter
OPENROUTER_API_KEY=sk-or-v1-xxxxxx

# GitHub Personal Access Token (for Copilot factory)
GITHUB_TOKEN=ghp_xxxxxx
```

### Optional Keys

```env
# Primary Ollama (local)
OLLAMA_HOST=http://localhost:11434

# Secondary Ollama (Docker — doubles cloud quota via failover/round-robin)
OLLAMA_HOST_SECONDARY=http://localhost:11435
OLLAMA_API_KEY_SECONDARY=your-secondary-key
OLLAMA_LOAD_STRATEGY=failover

# Slack alerts for agent notifications
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz

# Streamlit GUI session encryption
STREAMLIT_SECRET_KEY=your_secret_key
```

> ⚠️ **Never commit `.env`** — it's excluded by 70+ gitignore rules.

---

## Step 3 — Start Core Services

```bash
# Start the Gateway (message bus for agent orchestration)
uv run uvicorn agents.gateway.bus:app --host 0.0.0.0 --port 8000

# In a new terminal — Start the GUI
uv run streamlit run gui/app.py
```

Open [http://localhost:8501](http://localhost:8501) to access the Cognitive SOC dashboard.

---

## Step 4 — Run the Test Suite

```bash
# Run all 197 tests
uv run pytest tests/test_installation.py -v

# Run the full preflight (build + lint + test)
npm run preflight
```

All 197 tests should pass with 0 skipped.

---

## Step 5 — Set Up Jules (Your Agent Swarm)

Jules is the autonomous maintenance and evolution agent:

1. Visit [jules.google.com](https://jules.google.com) and connect your forked repo
2. Configure task pipelines in `config/jules_tasks.yaml`
3. Set your cadence preferences:

| Cadence | Tasks |
|---------|-------|
| **Daily** | Dream Mode cycles, lint & test sweeps, HLF token audits, ALIGN compliance scans |
| **Weekly** | Architecture diagram sync, README refresh, Eleven-Hat reviews, grammar evolution proposals |
| **Monthly** | Full integration test sweeps, tier promotion reviews, security assessments, release cuts |

Jules will auto-create PRs with grammar proposals, test fixes, and dependency updates.

> **🔮 Exponential Improvement:** Each fork running its own Jules swarm contributes to HLF grammar evolution (RFC 9006). Your agents propose grammar improvements that get validated and merged upstream — making HLF better for everyone.

---

## Step 6 — Deploy Your GitHub Pages Dashboard

1. Go to **Settings → Pages** in your fork
2. Set Source to **GitHub Actions → Static HTML**
3. The `docs/` directory auto-deploys with:
   - Live HLF translation chat demo (unlimited on your fork!)
   - Active agent PR feed
   - CI/Actions status
   - Recent commit timeline
   - Jules update cadence schedule

---

## Step 7 — Launch the Copilot Factory

```bash
# See available templates
python scripts/copilot_factory.py list-templates

# Available templates:
# - hlf-evolve    : Propose HLF grammar evolution
# - hlf-test      : Generate HLF test coverage
# - align-harden  : Harden ALIGN governance policies
# - gui-feature   : Build new GUI component
# - ci-fix        : Fix CI/CD pipeline issues

# Dry run first
python scripts/copilot_factory.py hlf-test --dry-run

# Dispatch live
python scripts/copilot_factory.py hlf-evolve
```

---

## Architecture Overview

```
User (NLP) → HLF Compiler (LALR(1)) → ALIGN Validator → Agent Gateway → Agent Swarm
                                                              ↓
                                              ┌─────────────────────────────┐
                                              │   Ollama Primary (:11434)   │
                                              │   Ollama Secondary (:11435) │
                                              │   (failover / round-robin)  │
                                              └─────────────────────────────┘
                                                              ↓
                                                    HLF Response → NLP Response
```

**Key Components:**
- **HLF Compiler** (`hlf/hlfc.py`) — LALR(1) parser with inline grammar
- **ALIGN Ledger** (`governance/`) — Zero-trust policy enforcement
- **Gateway** (`agents/gateway/`) — Redis-backed message bus & router
- **Model Registry** (`agents/model_registry.py`) — SQL-backed, tier-aware
- **GUI** (`gui/app.py`) — Streamlit Cognitive SOC with dark mode & dual Ollama transparency panel
- **Dual Ollama** — Failover/round-robin across primary (local) and secondary (Docker) instances

---

## Key Documentation

| Document | Purpose |
|----------|---------|
| `source_knowlwedge/HLF_The_Hieroglyphic_Logic_Framework_for_Agentic_Orchestration.md` | HLF paradigm overview |
| `source_knowlwedge/RFC_9005_The_HLF_Native_Language_Specification_v3.0.md` | Language spec v3.0 |
| `source_knowlwedge/The_HLF_v3.0_Protocol_Transparent_Compression_in_Systems_Programming.md` | Compression benchmarks |
| `build_notes/HLF_Core_A_Sovereign_Architecture_for_Secure_Agentic_OS.md` | Architecture deep-dive |
| `AGENTS.md` | Security invariants & test conventions |

---

## Why HLF?

> *"HLF is the Assembly Language of thought."*

- **60-67% token compression** — Agents sync microservice architectures in 50 tokens instead of 5,000
- **Sub-millisecond parsing** — Deterministic LALR(1), no LLM needed for parsing
- **Zero-trust by grammar** — Agents literally cannot express unauthorized instructions
- **Self-evolving** — Each swarm proposes grammar improvements (RFC 9006)
- **Built-in confidence** — Epistemic modifiers (ρ) trigger HITL when agents doubt themselves

---

*Built with 🤖 by the Sovereign Stack — [GitHub Repository](https://github.com/Grumpified-OGGVCT/Sovereign_Agentic_OS_with_HLF)*
