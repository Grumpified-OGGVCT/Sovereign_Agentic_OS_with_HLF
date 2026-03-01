# AGENTS.md — Jules Contextual Guide

> This file helps [Jules](https://jules.google/) understand the Sovereign Agentic OS with HLF repository.

## Architecture Overview

This is a **Sovereign Agentic OS** — a multi-layer AI operating system built around the
**Hieroglyphic Language Format (HLF)**, a compressed DSL for AI-to-AI communication.

| Layer | Name | Key Files |
|-------|------|-----------|
| 1 | Physical / ACFS | `acfs.manifest.yaml`, `docker-compose.yml`, `security/seccomp.json` |
| 2 | Kernel / Identity | `agents/core/memory_scribe.py`, `agents/core/dream_state.py` |
| 3 | Service Bus | `agents/gateway/bus.py` (FastAPI), `agents/gateway/router.py` (MoMA Router) |
| 4 | Logic / HLF | `hlf/hlfc.py` (Lark parser), `hlf/hlffmt.py`, `hlf/hlflint.py` |
| 5 | Data / Registry | `agents/core/db.py` (SQLite registry), `data/registry.db` |
| 6 | Governance | `agents/gateway/sentinel_gate.py`, `governance/ALIGN_LEDGER.yaml` |
| 7 | Observability | `agents/core/logger.py` (ALS with Merkle chains) |

## Project Layout

```
Sovereign_Agentic_OS_with_HLF/
├── agents/gateway/   # FastAPI bus + MoMA router + Sentinel gate
├── agents/core/      # Agent executor, logger, memory, db.py
├── config/           # settings.json (central config, no secrets)
├── governance/       # ALIGN ledger, HLF grammar, host functions
├── hlf/              # HLF compiler, formatter, linter
├── gui/              # Streamlit dashboard
├── tests/            # pytest test suite (use `uv run python -m pytest tests/ -v`)
├── scripts/          # Utility scripts
└── data/             # SQLite databases (registry.db, memory.sqlite3)
```

## Test Conventions

- **Framework:** `pytest` with `FastAPI TestClient` for integration tests
- **Run all tests:** `uv run python -m pytest tests/ -v --tb=short`
- **Mocking:** Redis interactions are mocked via `unittest.mock.AsyncMock`
- **Isolation:** `test_db.py` uses `:memory:` SQLite — no file cleanup needed
- **Naming:** `test_<feature>.py` with functions named `test_<scenario>()`

## Security Invariants (DO NOT VIOLATE)

1. **Cloud-First Isolation:** Local models NEVER mix into cloud tier walk
2. **Gas Limit Enforcement:** Every routing decision consumes gas via `consume_gas_async()`
3. **ALIGN Ledger:** All intents must pass through `enforce_align()` before routing
4. **4GB RAM Constraint:** Use stdlib `sqlite3` only — no heavy ORM
5. **Merkle-Chain Tracing:** All ALS logs chain via `ALSLogger.log()`
6. **Sentinel Passthrough:** Intents pass `enforce_align()` before any model dispatch

## Key Models & Data Flow

```
User Intent → bus.py → Rate Limit → HLF Lint → ALIGN Enforce → Gas Check
  → Nonce Check → router.py (route_request) → AgentProfile
  → main.py (execute_intent) → Ollama / OpenRouter → Response
```

## Dependency Management

- **Python packages:** Managed via `uv` (deterministic lockfile)
- **Install:** `uv sync`
- **Run commands:** Always prefix with `uv run`

## Companion Project: ollama-matrix-sync

Located at `../ollama-matrix-sync/`, this pipeline benchmarks and scores Ollama models.
It now supports `--registry-db` to persist scores to the SQL registry.
Run: `uv run python -m ollama_matrix_sync.pipeline --help`
