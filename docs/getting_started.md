# Getting Started

> **Grammar Version:** HLF v0.4.0 | **Updated:** 2026-03-10

## Prerequisites

- **Docker 24+** — required for containerised services (gateway, Ollama, Redis)
- **Python 3.12+** — runtime environment for all agents and CLI tools
- **[uv](https://github.com/astral-sh/uv)** — fast Python package manager (`pip install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **Redis 7+** *(optional for local dev)* — used by the gateway bus and rate limiter

---

## Quick Start

```bash
# 1. Copy the example environment file and fill in your API keys
cp .env.example .env

# 2. Run the one-shot bootstrap (Docker build + healthchecks + Ollama model pull)
bash bootstrap_all_in_one.sh
```

The bootstrap script prints `[SOVEREIGN OS GENESIS BLOCK INITIALIZED. AWAITING INTENT.]` when every service is healthy.

---

## Deployment Tiers

Set `DEPLOYMENT_TIER` in `.env` before running. The OS auto-selects configuration, networking privileges, and security boundaries:

| Tier | Docker Profile | Gas Bucket | Context Tokens | Use case |
|------|----------------|------------|----------------|----------|
| `hearth` | *(default)* | 1,000 | 8,192 | Home / personal use |
| `forge` | `forge` | 10,000 | 16,384 | Professional / team use |
| `sovereign` | `sovereign` | 100,000 | 32,768 | Enterprise / air-gapped |

```bash
# Example: start the forge profile
DEPLOYMENT_TIER=forge bash bootstrap_all_in_one.sh
```

---

## Local Development Setup

```bash
# Install Python dependencies
uv sync

# Run the full test suite
uv run pytest tests/ -v

# Compile a sample HLF program
uv run python -m hlf.hlfc tests/fixtures/hello_world.hlf

# Lint a sample HLF program
uv run python -m hlf.hlflint tests/fixtures/hello_world.hlf

# Format a HLF program in-place
uv run python -m hlf.hlffmt tests/fixtures/hello_world.hlf --in-place
```

---

## HLF Quick Reference (v0.4.0)

Every HLF program must start with a version header and end with `Ω`.

```hlf
[HLF-v3]
[INTENT] analyze /security/seccomp.json
[CONSTRAINT] mode="read-only"
[EXPECT] vulnerability_report
[RESULT] code=0 message="ok"
Ω
```

### Key Operators (RFC 9005 v3.0)

| Operator | Meaning | Example |
|----------|---------|---------|
| `↦ τ(tool)` | Execute tool | `↦ τ(io.fs.read) "/etc/config"` |
| `⊎ … ⇒ … ⇌` | If / then / else | `⊎ ok ⇒ [RESULT] 0 "done" ⇌ [RESULT] 1 "fail"` |
| `name ← value` | Assignment | `count ← 42` |
| `∥ [ … ]` | Parallel execution | `∥ [ [TASK] a, [TASK] b ]` |
| `⋈ [ … ] →` | Sync barrier | `⋈ [ a, b ] → [MERGE] result` |
| `name ≡ { … }` | Struct definition | `Config ≡ { host:𝕊, port:ℕ }` |
| `_{ρ:0.9}` | Confidence score | `result_{ρ:0.9} ← 42` |

See [`docs/HLF_GRAMMAR_REFERENCE.md`](HLF_GRAMMAR_REFERENCE.md) for the complete operator catalog, and [`docs/language-reference.md`](language-reference.md) for the full tag/glyph reference.

---

## Launching the GUI (C-SOC Dashboard)

The Cognitive SOC dashboard is a Streamlit app providing real-time visibility into agent activity, routing decisions, and memory.

```bash
uv run streamlit run gui/app.py
```

Open `http://localhost:8501` in your browser. The dashboard includes:

- **Routing Trace** — which model was selected, gas consumed, tier walk log
- **ALS Log Stream** — live Merkle-chain event feed
- **HLF Compiler Preview** — enter HLF text and see the JSON AST in real-time
- **Dream State Status** — nightly cycle progress and DSPy regression results
- **Agent Registry** — all active AgentProfile records from the SQL registry

---

## Starting the MCP Server

The Sovereign OS exposes a Model Context Protocol (MCP) server for IDE integration (Antigravity, Jules, Copilot).

```bash
# Option A: via the run menu
bash run.bat  # then select Option 3

# Option B: direct launch
uv run python -m mcp.server --auto-launch
```

Available MCP tools: `check_health`, `dispatch_intent`, `query_memory`, `run_dream_cycle`, `get_routing_trace`, `list_agents`, `get_align_status`, `get_gas_remaining`.

---

## Running the 6-Gate Security Pipeline Manually

Every intent passes through 6 deterministic gates. You can exercise them individually:

```bash
# Gate 1: structural validation
python3 -c "from hlf import validate_hlf; print(validate_hlf('[INTENT] test \"x\"'))"

# Gate 2: LALR(1) compile
uv run python -m hlf.hlfc tests/fixtures/hello_world.hlf

# Gate 3: lint (token budget + gas + unused vars)
uv run python -m hlf.hlflint tests/fixtures/hello_world.hlf

# Gate 4: ALIGN check
python3 -c "from agents.gateway.sentinel_gate import enforce_align; print(enforce_align('[INTENT] hello Ω'))"
```

---

## Environment Variables Reference

Key variables from `.env.example`:

| Variable | Description | Default |
|----------|-------------|---------|
| `DEPLOYMENT_TIER` | `hearth` / `forge` / `sovereign` | `hearth` |
| `OLLAMA_HOST` | Ollama primary endpoint | `http://ollama-matrix:11434` |
| `PRIMARY_MODEL` | Routing model for complex intents | *(set in settings.json)* |
| `REDIS_URL` | Redis broker connection string | `redis://redis-broker:6379/0` |
| `MAX_GAS_LIMIT` | Per-intent AST node limit | `10` |
| `MAX_CONTEXT_TOKENS` | Token window for this tier | `8192` |

All model names flow through `config/settings.json` — never hardcode model names directly.

---

## Architecture

See `README.md` for the full Mermaid architecture diagram and the system overview. Key component files:

| Layer | File | Purpose |
|-------|------|---------|
| Gateway | `agents/gateway/bus.py` | FastAPI intent ingestion, rate limiting, nonce check |
| Router | `agents/gateway/router.py` | MoMA 3-phase tier walk, gas metering |
| Security | `agents/gateway/sentinel_gate.py` | ALIGN Ledger enforcement |
| Executor | `agents/core/main.py` | Intent execution, Redis stream consumer |
| Memory | `agents/core/memory_scribe.py` | SQLite WAL writer, vector embeddings |
| HLF Compiler | `hlf/hlfc.py` | LALR(1) → JSON AST |
| Bytecode VM | `hlf/bytecode.py` | Stack-machine executor |
| GUI | `gui/app.py` | Streamlit C-SOC dashboard |

---

## Troubleshooting

### `uv: command not found`
Install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh` then restart your shell.

### Docker healthcheck loops
Verify Ollama is pulling models: `docker logs ollama-matrix`. Model pulls can take several minutes on first run.

### `HlfAlignViolation` errors
Your HLF payload matched an ALIGN Ledger rule. Check `governance/ALIGN_LEDGER.yaml` for the blocking pattern. Common triggers: `curl`, `wget`, `os.system`, `eval(`.

### Gas budget exceeded (HTTP 429)
Reduce the number of AST nodes in your intent, or increase `MAX_GAS_LIMIT` in `.env` (max advised: tier bucket / 10).

### Redis connection refused
Start Redis locally: `docker run -d -p 6379:6379 redis:7-alpine` or set `REDIS_URL` to your instance.

### Collection errors in pytest
Run `uv run pytest tests/ -v --ignore=tests/test_installation.py` to skip installation-only tests that require a live Docker environment.

---

## Next Steps

- Read the [HLF Grammar Reference](HLF_GRAMMAR_REFERENCE.md) to learn all operators
- Browse [docs/HLF_REFERENCE.md](HLF_REFERENCE.md) for the comprehensive language spec
- Read [docs/WALKTHROUGH.md](WALKTHROUGH.md) for a guided end-to-end scenario
- See [docs/Automated_Runner_Setup_Guide.md](Automated_Runner_Setup_Guide.md) for CI/CD integration
- Explore [docs/openclaw_integration.md](openclaw_integration.md) for external binary tool orchestration
