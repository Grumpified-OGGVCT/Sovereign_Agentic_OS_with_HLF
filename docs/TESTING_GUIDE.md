# Testing Guide

> **Sovereign Agentic OS — Test Suite Reference**
> Updated: 2026-03-10 | 1289+ tests collected across 89 test files

This guide explains how to run the test suite, what each test file covers, and how to add new tests following the project conventions.

---

## Table of Contents

1. [Running Tests](#1-running-tests)
2. [Test Organisation](#2-test-organisation)
3. [Test Conventions](#3-test-conventions)
4. [Writing HLF Tests](#4-writing-hlf-tests)
5. [CI Pipeline](#5-ci-pipeline)
6. [Anti-Reductionist Mandate](#6-anti-reductionist-mandate)

---

## 1. Running Tests

### Full suite

```bash
uv run python -m pytest tests/ -v
```

### Targeted runs

```bash
# Single file
uv run python -m pytest tests/test_hlf.py -v

# By keyword
uv run python -m pytest tests/ -k "align" -v

# Skip installation tests (requires live Docker)
uv run python -m pytest tests/ --ignore=tests/test_installation.py -v

# Stop after first failure
uv run python -m pytest tests/ -x -v

# With coverage
uv run python -m pytest tests/ --cov=agents --cov=hlf --cov-report=term-missing
```

### Collect-only (count without running)

```bash
uv run python -m pytest tests/ --collect-only -q
```

---

## 2. Test Organisation

Tests live in `tests/`. Each file is named `test_<feature>.py`. Key files:

### HLF Language & Toolchain

| File | What it tests |
|------|---------------|
| `test_hlf.py` | Compiler (hlfc), formatter (hlffmt), linter (hlflint), grammar round-trips |
| `test_hlfc_cli.py` | `hlfc` CLI argument handling and exit codes |
| `test_bytecode.py` | Bytecode VM — opcodes, gas metering, SHA-256 checksums, disassembler |
| `test_runtime.py` | `hlf/runtime.py` — tag dispatch, SET/FUNCTION/RESULT semantics |
| `test_grammar_roundtrip.py` | Parse → AST → format → re-parse → structural equality |
| `test_hlflsp.py` | Language Server Protocol diagnostics for HLF |

### Security & Governance

| File | What it tests |
|------|---------------|
| `test_align_ledger.py` | All 9 ALIGN rules (R-001–R-009) — blocked payloads return 403 |
| `test_policy.py` | Full intent replay matrix: valid→202, malformed→422, align-blocked→403, gas→429, nonce→409 |
| `test_acfs.py` | ACFS directory permissions match `acfs.manifest.yaml` |
| `test_acfs_worktree.py` | Git worktree isolation, shadow commits, stale cleanup |
| `test_blast_radius.py` | Chaos scenarios — what breaks when each service is unavailable |
| `test_credential_vault.py` | Vault encrypt/decrypt round-trips |

### Gateway & Routing

| File | What it tests |
|------|---------------|
| `test_agent_bus.py` | FastAPI gateway endpoints, middleware chain, rate limiting |
| `test_bus_bytecode.py` | HLF bytecode payloads through the gateway bus |
| `test_e2e_pipeline.py` | Full end-to-end intent path from HTTP to execution |

### Memory & Storage

| File | What it tests |
|------|---------------|
| `test_memory.py` | SQLite WAL mode, vector column, sqlite-vec extension |
| `test_db.py` | 9-table registry schema, AgentProfile, seed_aegis_templates |
| `test_dream_state.py` | Dream cycle scheduling, DSPy regression, log truncation |
| `test_als_schema.py` | ALS log JSON schema, Merkle chain integrity |

### Agent Orchestration

| File | What it tests |
|------|---------------|
| `test_crew_orchestrator.py` | PlanExecutor → SpindleDAG → CodeAgent/BuildAgent pipeline |
| `test_code_agent.py` | CodeAgent task execution, ALIGN-safe code generation |
| `test_build_agent.py` | BuildAgent compilation and test running |
| `test_aegis_nexus.py` | 14-Hat Aegis-Nexus engine, all hat dispatch |
| `test_aegis_daemons.py` | Sentinel / Scribe / Arbiter daemon lifecycle |
| `test_arbiter.py` | Arbiter adjudication logic |

### HLF Toolchain Extensions

| File | What it tests |
|------|---------------|
| `test_tool_forge.py` | Tool Forge — loop detection, 3-gate validation, persistent storage |
| `test_formal_verifier.py` | HLF program formal correctness properties |
| `test_openclaw_summarize.py` | OPENCLAW_SUMMARIZE host function config validation |

### Bootstrap & Installation

| File | What it tests |
|------|---------------|
| `test_bootstrap.py` | `bootstrap_all_in_one.sh` — compose healthchecks, model pull |
| `test_installation.py` | 65 deep verification tests (requires live Docker environment) |

---

## 3. Test Conventions

The project uses **pytest** with `FastAPI TestClient` for integration tests.

### Naming
```python
# test file:     test_<feature>.py
# test function: test_<scenario>()

def test_align_r001_blocks_shell_escape():
    ...

def test_gas_budget_exhausted_returns_429():
    ...
```

### Mocking
Redis interactions are mocked via `unittest.mock.AsyncMock`:

```python
from unittest.mock import AsyncMock, patch

@patch("agents.gateway.bus.redis_client")
async def test_rate_limit(mock_redis):
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock(return_value=True)
    ...
```

### Database isolation
Tests that write to SQLite use `:memory:` databases — no file cleanup needed:

```python
import sqlite3

def test_fact_store_insert():
    conn = sqlite3.connect(":memory:")
    # ... create schema, insert, assert
    conn.close()
```

### Fixtures
Shared fixtures live in `tests/conftest.py`. HLF source fixtures live in `tests/fixtures/`:

```
tests/fixtures/
├── hello_world.hlf
├── security_audit.hlf
├── multi_agent_delegation.hlf
├── db_migration.hlf
├── content_delegation.hlf
├── log_analysis.hlf
└── stack_deployment.hlf
```

---

## 4. Writing HLF Tests

### Compile assertion

```python
from hlf.hlfc import HLFCompiler

def test_intent_compiles():
    compiler = HLFCompiler()
    result = compiler.compile('[HLF-v3]\n[INTENT] test "target"\nΩ')
    assert result["version"] == "0.4.0"
    assert result["program"][0]["tag"] == "INTENT"
```

### ALIGN violation assertion

```python
from agents.gateway.sentinel_gate import enforce_align

def test_shell_escape_blocked():
    blocked, rule_id = enforce_align("[ACTION] run /bin/sh")
    assert blocked is True
    assert rule_id == "R-001"
```

### Round-trip test

```python
from hlf.hlfc import HLFCompiler
from hlf.hlffmt import HLFFormatter

def test_roundtrip(hlf_source):
    compiler = HLFCompiler()
    ast1 = compiler.compile(hlf_source)
    formatter = HLFFormatter()
    formatted = formatter.format(hlf_source)
    ast2 = compiler.compile(formatted)
    assert ast1["program"] == ast2["program"]
```

---

## 5. CI Pipeline

Tests run in GitHub Actions (`.github/workflows/ci.yml`) in this order:

1. **`ensure-no-sandbox-in-prod`** — grep for `OLLAMA_ALLOW_OPENCLAW=1` in compose files; fail if found
2. **`lint-and-test`** (needs step 1):
   - `uv sync --frozen`
   - `python scripts/hlf_token_lint.py` — fail if any intent > 30 tokens
   - `pre-commit run --all-files` — ruff lint + type checks
   - `pytest tests/ -v`
3. **`validate-models`** — verify `settings.json` has model whitelists for all three tiers

All three jobs must pass before a PR can be merged.

---

## 6. Anti-Reductionist Mandate

Tests are **additive-only**. The following are hard rules:

- **Never delete tests** — even if they fail, fix the code instead
- **Never remove assertions** from existing tests — only add more
- **Never mock away security gates** in production test paths
- **Test count must be ≥ baseline** — every PR is checked with `pytest --collect-only`
- **Coverage must not decrease** — scheduled baseline comparisons via `pytest --cov`

If a test is genuinely obsolete (e.g., feature removed by governance decision), it must be marked `@pytest.mark.skip` with a reason, not deleted.

```python
@pytest.mark.skip(reason="Feature gated behind sovereign tier — not available in CI")
def test_ebpf_syscall_filter():
    ...
```
