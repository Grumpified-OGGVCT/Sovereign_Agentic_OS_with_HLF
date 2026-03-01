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

---

## Transparency-First Mandate

Jules MUST continuously assess and enhance system-wide transparency. This is not optional.

### Rules

1. **GUI agent visibility** — Every agent action, routing decision, and model selection MUST be visible in `gui/app.py`. Add real-time panels showing:
   - Current agent activity (what is being processed, by which model)
   - HLF compilation status and real-time translation previews
   - Routing trace visualization (which tier, which model, gas consumed)
   - ALS log stream (live Merkle-chain event feed)
2. **Adjustable user levels** — Implement resource and token governance levels:
   - `minimal` — summary-only view, low token budget
   - `standard` — full agent activity, moderate token budget
   - `detailed` — raw HLF output, full routing traces, max token budget
3. **Inter-agent communication** — All agent-to-agent messages MUST be logged and displayable
4. **Real-time HLF translation** — Show the user what HLF the system generates, with natural-language equivalents
5. **Run through Six Hats** — Every transparency enhancement MUST pass the Six Hats review protocol
6. **Never reduce visibility** — You may ONLY add transparency features. Never remove, simplify, or hide existing ones.

## Self-Capability Tracking

Jules MUST maintain awareness of its own capabilities and limitations.

### Rules

1. **Capability manifest** — Maintain a `config/jules_capabilities.yaml` listing:
   - What Jules CAN currently do (grammar expansion, test writing, CI fixing, etc.)
   - What Jules CANNOT yet do (blocked by missing APIs, permissions, tooling)
   - What Jules COULD do with specific enhancements (propose them as GitHub issues)
2. **Enhancement proposals** — When Jules identifies a way to expand its usefulness:
   - Create a GitHub issue with label `jules-enhancement`
   - Include: what it would enable, what it requires, estimated effort, risk assessment
   - Pass the proposal through Six Hats before submitting
3. **Capability growth log** — Track capability expansions over time in `reports/jules_capability_log.md`
4. **Never overstate capabilities** — If Jules cannot verify it can do something, it MUST say so

## Gemini Model Integration

Jules MUST track and integrate upgraded Gemini models and services.

### Rules

1. **Model registry updates** — When new Gemini models are available (Gemini 2.5 Pro, Flash, etc.), add them to the SQL registry with proper tiers and scores
2. **Service integration** — Track Gemini-specific services (vision, code execution, grounding) and propose integration points
3. **Benchmark comparison** — Run new Gemini models through the ollama-matrix-sync pipeline where applicable
4. **API key management** — Ensure Gemini API keys are properly stored in `.env` and handled by `Settings` classes
5. **Fallback chains** — Gemini models should participate in the 3-phase tier walk where appropriate

## HLF Agent Maximization

The Jules agent handling HLF and related components MUST be maximized for usefulness and power.

### Rules

1. **Grammar evolution** — Actively propose new HLF tags, syntax extensions, and expressive power improvements
2. **Compiler hardening** — Expand `hlfc.py` test coverage, edge case handling, and error messages
3. **Linter expansion** — Add new lint rules to `hlflint.py` for security, performance, and best practices
4. **Formatter improvements** — Enhance `hlffmt.py` for canonical formatting and readability
5. **Runtime capabilities** — Expand `hlfrun.py` with new built-in functions, action types, and host function bindings
6. **Test corpus growth** — Continuously expand the HLF test corpus with real-world examples, edge cases, and adversarial inputs
7. **Documentation** — Keep HLF documentation current with every grammar change
8. **Never simplify** — HLF changes MUST be additive. Never remove syntax, reduce expressiveness, or simplify existing capabilities.

## Anti-Reduction Checklist (MANDATORY for every Jules PR)

- [ ] No files deleted
- [ ] No tests removed or weakened
- [ ] No features simplified or scope-reduced
- [ ] No transparency features hidden or removed
- [ ] No governance rules weakened
- [ ] No model capabilities reduced
- [ ] Coverage >= baseline
- [ ] Test count >= baseline
- [ ] Six Hats review completed
- [ ] CoVE validation passed (compact or full)

