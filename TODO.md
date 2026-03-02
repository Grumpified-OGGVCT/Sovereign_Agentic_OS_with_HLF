# Sovereign Agentic OS — Master Roadmap & Build Progress

> Last audited: 2026-03-01 | Tests: 197 | HLF Fixtures: 7 | Grammar: v0.4.0

---

## Phase I-III: Design & Specification ✅ COMPLETE
- [x] Layers 1-7 deep-dive analysis and specification
- [x] 11-Hat adversarial audit cycle (6 De Bono + 5 Sovereign OS extended)
- [x] Master Build Plan synthesis (994 lines)
- [x] Copilot scaffold prompt generation & execution

## Phase IV: Scaffold & Foundation ✅ COMPLETE

### ACFS & Environment (Phase 1.1)
- [x] Exact ACFS directory tree created
- [x] `acfs.manifest.yaml` — directories, permissions, SHA256 checksums
- [x] `.env.example` — all required vars (DEPLOYMENT_TIER, OLLAMA_HOST, REDIS_URL, MAX_GAS_LIMIT, VAULT_ADDR)
- [x] `config/settings.json` — central config (no secrets)
- [x] `.gitignore` — unlocked governance/security/observability/ci

### Docker & Isolation (Phase 1.2-1.3)
- [x] `Dockerfile.base` — python:3.12-slim, uv, non-root, SIGUSR1 trap
- [x] `docker-compose.yml` — 6 services (gateway, executor, redis, memory, ollama, orchestrator)
- [x] `security/seccomp.json` — syscall deny list
- [x] Redis `config/redis.conf` — persistence config per tier
- [x] `dapr/components/pubsub.yaml` — Dapr pub/sub
- [x] `dapr/components/statestore.yaml` — Dapr state store

### Governance & ALIGN (Phase 1.1 + 3.3)
- [x] `governance/ALIGN_LEDGER.yaml` — R-001 through R-008
- [x] `governance/hls.yaml` — HLF grammar spec
- [x] `governance/templates/dictionary.json` — v0.2 schema with typed tags
- [x] `governance/templates/system_prompt.txt` — Zero-Shot Grammar
- [x] `governance/host_functions.json` — host function registry
- [x] `governance/kya_init.sh` — KYA cert generation
- [x] `governance/service_contracts.yaml`
- [x] `governance/openclaw_strategies.yaml`
- [x] `governance/module_import_rules.yaml`
- [x] `governance/bytecode_spec.yaml`
- [x] `governance/dapr_grpc.proto`

### HLF Toolkit (Phase 3.1 + 4.4)
- [x] `hlf/hlfc.py` — Lark LALR(1) parser (compiles .hlf → JSON AST)
- [x] `hlf/hlffmt.py` — Canonical formatter
- [x] `hlf/hlflint.py` — Static analyzer (unused vars, gas, token budget)
- [x] Fix roundtrip: hlffmt output must re-parse cleanly via hlfc
- [x] `hlf/__init__.py` — validate_hlf() regex hook for ASB
- [x] `scripts/hlf_token_lint.py` — pre-commit token budget linter

### Gateway & ASB (Phase 2.1-2.2)
- [x] `agents/gateway/bus.py` — FastAPI, rate limiter, ALIGN enforcement, ULID nonces
- [x] `agents/gateway/router.py` — MoMA Router, VRAM checks, gas scripting
- [x] `agents/gateway/sentinel_gate.py` — ALIGN Ledger enforcer
- [x] Wire ASB to use hlfc JSON AST instead of regex
- [x] Dapr pub/sub integration
- [x] Circuit breaker mechanism
- [x] Web search mediation

### Core Agents (Phase 3.2-3.3 + 4.1-4.3)
- [x] `agents/core/main.py` — Agent executor entrypoint, SIGUSR1 trap
- [x] `agents/core/memory_scribe.py` — SQLite + Redis consumer
- [x] `agents/core/dream_state.py` — Context compression & archival
- [x] `agents/core/tool_forge.py` — Dynamic tool generation
- [x] `agents/core/legacy_bridge.py` — HLF → REST translation
- [x] `agents/core/logger.py` — ALS logger with Merkle chain
- [x] `agents/core/ast_validator.py` — Python code security scanner
- [x] SQLite schema migration (identity_core, rolling_context, fact_store)
- [x] Vector DB (sqlite-vec) installation & FLOAT32[768] columns
- [x] Active Context Tiering
- [x] Fractal Summarization
- [x] Dead Man's Switch

### Bootstrap & Tests (Phase 4.4)
- [x] `bootstrap_all_in_one.sh` — 8-step genesis sequence with shutdown trap
- [x] Test suite: 197 tests passing
- [x] Alembic migrations
- [x] `tests/fixtures/hello_world.hlf` — update to match Appendix A.7

---

## Infrastructure Roadmap (Phases 1-5)

### Infra Phase 1: SQL Registry (`db.py`) ✅ COMPLETE
- [x] Create `agents/core/db.py` (485 lines)
- [x] Python Enums: `ModelTier` (S/A+/A/A-/B+/B/C/D), `Provider` (ollama/openrouter/cloud)
- [x] 7 SQLite tables: `snapshots`, `models`, `model_tiers`, `user_local_inventory`, `local_model_metadata`, `agent_templates`, `model_equivalents`
- [x] Add `policy_bundles` and `model_feedback` tables (Telemetry & Governance)
- [x] CRUD helpers: `get_active_snapshot()`, `get_models_by_tier()`, `get_all_models()`, `get_local_inventory()`, `upsert_model()`, etc.
- [x] Map existing `score_to_tier()` letter grades → `ModelTier` enum via `TIER_MAP`
- [x] Unit tests for schema integrity (`tests/test_db.py`)

### Infra Phase 2: Pipeline Upgrade (Global + Local Cycles) 🟡 ~40%
- [ ] Refactor `run_pipeline()` to persist to `registry.db` alongside existing CSV artifacts
- [ ] Implement snapshot creation and atomic promotion logic (call `create_snapshot()` + `promote_snapshot()`)
- [ ] Add 6-hour scheduler/cron trigger
- [x] Implement Local Inventory Heartbeat in `bus.py` (`sync_inventory()` endpoint)
- [x] Heartbeat calls `fetch_tags(LOCAL_OLLAMA)` → upserts `user_local_inventory`

### Infra Phase 3: Routing Engine (The Invariant) ✅ COMPLETE
- [x] Replace `route_intent()` with `route_request()` in `router.py` (160 lines)
- [x] Implement 3-Phase Walk: Cloud Tiers S→D → Local Inventory → OpenRouter Handoff
- [x] Implement Pre-Routing Hooks (Specialized Overrides: devstral/qwen3-vl)
- [x] Implement Uncensored Lane (mistral-large-3 primary, dolphin-mistral-24b fallback)
- [x] Integrate existing `_is_cloud()`, `check_vram_threshold()`, `consume_gas_async()`
- [x] `AgentProfile` dataclass (10 fields: model, provider, tier, system_prompt, tools, restrictions, routing_trace, gas_remaining, confidence)
- [x] Tests: `test_router_v2.py` — 8 tests

### Infra Phase 4: Agent OS Wiring (Provisioning) 🟡 ~75%
- [x] Refactor `main.py` `_ollama_generate()` → `_ollama_generate_v2()` consuming `route_request()` output
- [x] Refactor `execute_intent()` to apply Agent Profile (model, provider, system_prompt, restrictions)
- [x] Wire `ALSLogger` for `ROUTING_DECISION` events (governance audit trail)

### Infra Phase 5: GUI & Governance ❌ ~5%
- [ ] Add Transparency Panel to `gui/app.py` (routing trace, snapshot version, tier display)
- [ ] Add Registry Management controls (trigger sync, view inventory, view model catalog)
- [ ] Add thumbs-up/down feedback to `model_feedback` table (CRUD exists in db.py, needs GUI surface)
- [ ] End-to-end verification (pipeline → registry → router → executor → GUI)

---

## HLF Language Roadmap (Phases 3, 5.1-5.3)

### HLF Phase 3: Core Language (~85% complete)
- [x] LALR(1) parser via Lark
- [x] 14 statement types in grammar (v0.4.0)
- [x] Immutable SET bindings with duplicate detection
- [x] Two-pass compilation (env collection → var expansion)
- [x] Ω / Omega terminator
- [x] 5 pure built-in functions
- [x] RFC 9005 operators: ↦τ, ⊎⇒⇌, ¬∩∪, ←, ::, ∥, ⋈, &, _{ρ:val}
- [x] RFC 9007 operators: ≡ (struct)
- [x] Glyph prefixes: ⌘ Ж ∇ ⩕ ⨝ Δ ~ § (now parsed, not ignored)
- [x] Math expressions: +, -, *, /, comparisons
- [x] InsAIts V2 human_readable on every AST node
- [x] format_correction() — Iterative Intervention Engine
- [x] docs/HLF_GRAMMAR_REFERENCE.md — authoritative operator catalog
- [ ] Runtime interpreter with gas metering
- [ ] Error-code propagation via RESULT
- [x] Regex validation gate (`validate_hlf`)
- [x] HLF linter middleware (token, gas, unused vars)
- [ ] `dictionary.json` arity/type enforcement at parse-time
- [ ] `hls.yaml` formal grammar spec (machine-readable BNF)
- [x] ALIGN enforcement middleware (`sentinel_gate.py`)
- [x] Nonce/ULID replay protection
- [x] Legacy Bridge Module (`decompress_hlf_to_rest`)

### HLF Phase 5.1: v0.3 Modules & Host Functions (~25% complete)
- [x] MODULE and IMPORT grammar rules
- [x] MODULE and IMPORT AST transformer
- [x] Tier-aware execution (hearth/forge/sovereign)
- [x] Host function dispatch architecture (ACTION → dispatcher)
- [x] 7 host function stubs documented
- [ ] Module runtime file loading + namespace merge
- [ ] Host function registry (`governance/host_functions.json`) — live dispatch
- [ ] OCI module distribution
- [ ] Module checksum validation
- [ ] ALIGN Rule R-008 (block raw OpenClaw keys)

### HLF Phase 5.2: v0.4 Byte-Code VM (0% — Future)

- [ ] Stack-machine byte-code compiler (`hlfc --emit-bytecode`)
- [ ] 32-instruction opcode set (PUSH, POP, CALL, RET, JMP, etc.)
- [ ] `.hlb` binary format (HLFv04 magic + LE uint32 opcodes)
- [ ] Wasm sandbox integration (Wasmtime)
- [ ] Dapr gRPC integration for runtime
- [ ] `hlfrun` interpreter for `.hlb` files

### HLF Phase 5.3: v0.5 Language DX (10% — Future)

- [ ] TextMate syntax highlighting (`hlf.tmLanguage.json`)
- [ ] Language Server Protocol (`hlflsp` via `pygls`)
- [ ] HLF REPL (`hlfsh`)
- [ ] Package manager (`hlfpm`)
- [ ] Test harness (`hlf-test`)
- [ ] MkDocs documentation site

---

## Key Metrics for Dashboard

| Metric | Current Value | Source |
| --- | --- | --- |
| Grammar statement types | 14 | `hlfc.py` `_GRAMMAR` |
| RFC 9005/9007 operators | 13 | `hlfc.py` `_GRAMMAR` |
| Terminal types | 10 | `hlfc.py` `_GRAMMAR` |
| Built-in functions | 5 | `hlfrun.py` `_BUILTIN_FUNCTIONS` |
| Host function stubs | 7 | `hlfrun.py` docstring |
| Toolchain size (lines) | ~680+ | `hlf/*.py` |
| Test count (total) | 200+ | pytest |
| Test count (HLF-specific) | 15 | `test_hlf.py` |
| Test pass rate | 100% | CI |
| Fixture files | 7 | `tests/fixtures/` |
| Dictionary tags | 7 | `dictionary.json` |
| Registered glyphs | 8 | `hlfc.py` GLYPH_PREFIX |
| Compiler version | 0.4.0 | `hlfc.compile()` |

---

## Priority Actions for Jules Agents

1. **Expand test fixtures** — Create 5-10 domain-specific `.hlf` files in `tests/fixtures/` (DevOps, Security, Creative, Architecture, Data tasks)
2. **Build benchmark script** — `scripts/hlf_benchmark.py` that tokenizes NLP vs HLF for real compression measurements
3. **Build metrics script** — `scripts/hlf_metrics.py` that scans codebase and outputs `docs/metrics.json`
4. **Complete MODULE runtime** — Implement file loading and namespace merge for `[IMPORT]` statements
5. **Create `hls.yaml`** — Machine-readable BNF grammar spec at `governance/hls.yaml`

---

## HLF Value Proposition (Reference)

HLF's efficiency story has two dimensions:

### 1. Token Compression

A full JSON agent instruction payload (~148-185 tokens) compresses to ~22-30 HLF tokens = **83-86% reduction**. In a 5-agent swarm, that's 615-775 tokens saved per round-trip.

### 2. Security Pipeline

Every intent passes through a **6-gate security pipeline** that JSON/natural language cannot provide:

1. `validate_hlf()` — Regex structural gate
2. `hlfc.compile()` — LALR(1) parse + type validation
3. `hlflint.lint()` — Token budget + gas + unused var detection
4. ALIGN enforcement — Regex block patterns (R-001 through R-008)
5. Gas budget — Per-intent + global per-tier Redis token bucket
6. Nonce check — ULID replay protection via Redis SETNX

> Traditional NLP/JSON payloads skip gates 1-3 entirely and require custom middleware for gates 4-6.

---

## Sovereign Backend Engine (Extended)

### V.1: Ollama Matrix Sync Pipeline

- [x] Integrate the `ollama-matrix-sync` benchmarking pipeline into the main OS
- [x] Pipeline→Registry bridge (`--registry-db --promote` flags)
- [ ] Wire the Gateway `router.py` to dispatch to Ollama dynamically based on matrix constraints (→ Infra Phase 2)

### V.2: ALIGN Ledger & Host Functions Validation

- [ ] Implement robust token validation in `hlfc.py` using `ALIGN_LEDGER.yaml`
- [ ] Enforce deterministic mathematical verification of HLF tokens against the ledger

### V.3: Tri-Perspective Aegis-Nexus Engine (Core Agents)

- [ ] Instantiate the Sentinel Agent (Red Hat / Security & PrivEsc checks)
- [ ] Instantiate the Scribe Agent (White Hat / Memory & Token Bloat)
- [ ] Instantiate the Arbiter Agent (Blue Hat / Exception Handling & Governance)
- [ ] Instantiate the Synthesizer Agent (Indigo Hat / Cross-Feature Architecture)
- [ ] Instantiate the Scout Agent (Cyan Hat / Innovation & Feasibility)
- [ ] Instantiate the Guardian Agent (Purple Hat / AI Safety & Compliance)
- [ ] Instantiate the Operator Agent (Orange Hat / DevOps & Automation)
- [ ] Instantiate the Compressor Agent (Silver Hat / Context & Token Optimization)

### V.4: OpenClaw Strategy Integration (Appendix B)

- [ ] Create the secure sandbox profile (`seccomp.json`)
- [ ] Implement Strategy B (Pure Tools) for approved functions

### V.5: Jules Integration (Continuous AI Agent) ✅ COMPLETE

- [x] Create `AGENTS.md` in repo root
- [x] `config/jules_tasks.yaml` with nightly/weekly/monthly schedules
- [x] `scripts/jules_dispatch.sh` — Issue → Session automation
- [x] CoVE + Eleven Hats review templates
- [x] 10-step daily pipeline configuration

### V.6: Metrics & Benchmarking ✅ COMPLETE

- [x] `scripts/hlf_metrics.py` — codebase scanner → `docs/metrics.json`
- [x] `scripts/hlf_benchmark.py` — tiktoken compression benchmark → `docs/benchmark.json`
- [x] 6 domain-specific `.hlf` test fixtures
- [x] `docs/HLF_PROGRESS.md` — progress tracking for Jules agent sync

### V.7: Live Demo & Documentation ✅ COMPLETE

- [x] GitHub Pages demo (`docs/index.html`) with dark mode
- [x] Model Router architecture popup
- [x] Infinite RAG Memory Matrix popup with HLF×RAG synergy
- [x] Translation quota system + owner exemption
- [x] Generated infographics (system architecture, registry flow, Jules pipeline, RAG comparison)
- [x] README overhaul with honest benchmark data + live demo links
