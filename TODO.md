# Sovereign Agentic OS — Master TODO

> Last updated: 2026-03-09 12:25 CST | Test baseline: 1,670 passing (100%)
> GitHub Issues: [#67](https://github.com/Grumpified-OGGVCT/Sovereign_Agentic_OS_with_HLF/issues/67), [#17](https://github.com/Grumpified-OGGVCT/Sovereign_Agentic_OS_with_HLF/issues/17), [#14](https://github.com/Grumpified-OGGVCT/Sovereign_Agentic_OS_with_HLF/issues/14), [#51](https://github.com/Grumpified-OGGVCT/Sovereign_Agentic_OS_with_HLF/issues/51)

---

## 🟢 Recently Shipped (2026-03-08)

- [x] z.AI Provider — GLM-5/4.6V/CogView-4/CogVideoX-3/OCR (37 tests)
- [x] z.AI Video Polling — `poll_video_status()`, `get_video_result()`, `zai.video_status` tool
- [x] HLF stdlib modules — 5 core modules: math, string, io, crypto, collections (28 tests)
- [x] Intent Capsules — Gateway Bus wiring, tier factories, CapsuleViolation → HTTP 403 (18 tests)
- [x] Native OS Bridge — Enterprise platform abstraction, 12 files, singleton, rate limiter (56 tests)
- [x] Agent Orchestration Layer — PlanExecutor, CodeAgent, BuildAgent (Wave 5)
- [x] Tool Ecosystem Pipeline — `hlf install`, CoVE gate, lockfiles, lazy-loading
- [x] Bytecode VM — stack-machine, `bytecode_spec.yaml`, assembler/disassembler
- [x] HLF LSP (`hlflsp`) — diagnostics, completions, hover, go-to-def, symbols (29 tests)
- [x] Daemon Bridge — SpindleEventBus↔DaemonEventBus translation + gas accounting (17 tests)
- [x] Daemon Test Suites — Sentinel (27), Scribe (29), Arbiter (28), OCI Client (30) = 114 tests
- [x] HLF Package Manager (`hlfpm`) — install/uninstall/search/freeze, lockfile (27 tests)
- [x] HLF Test Harness (`hlftest`) — runner, assertions, pytest plugin (23 tests)
- [x] HLF REPL (`hlfsh`) — persistent env, gas metering, 8 commands (27 tests)
- [x] Phase 5.1: OCI module distribution, module checksums ✅ DONE
- [ ] Phase 5.2: Wasm sandbox (Wasmtime), Dapr gRPC runtime, `.hlb` binary format
- [x] Phase 5.3: LSP, REPL, Package Manager, Test Harness ✅ DONE — only MkDocs site remaining

---

## ✅ Priority 1: Phase 5.1 — OCI Module Distribution (COMPLETE)

- [x] OCI module distribution — `OCIClient.push()` / `OCIClient.pull()`
- [x] `[IMPORT]` tag resolution via OCI — `ModuleLoader._resolve_oci()` with lazy client
- [x] `acfs.manifest.yaml` `modules:` section — checksums loaded by `_load_manifest_checksums()`
- [x] `settings.json` OCI config — `oci_registry`, `oci_namespace`, `oci_enabled`, `cache_dir`

---

## 🟠 Priority 2: GitHub Issue #17 — Aegis-Nexus Sentinel/Scribe/Arbiter

*Runtime daemons — partially shipped, wiring remaining*

- [x] Sentinel runtime daemon — `sentinel.py` (292 lines) + 27 tests
- [x] Scribe runtime daemon — `scribe.py` (281 lines) + 29 tests
- [x] Arbiter runtime daemon — `arbiter.py` (435 lines) + 28 tests
- [x] Daemon Bridge — event translation + gas accounting (17 tests)
- [x] Agent profiles in `config/personas/` — sentinel.md, scribe.md, arbiter.md (8KB+ each)
- [x] DaemonManager → SpindleEventBus auto-wiring on start (Batch B, 27 tests)
- [ ] Per-agent gas dashboard report API
- [ ] ASB Redis Streams inter-agent communication wiring

> **Resolved**: All 5 `hlf_programs/` now compile end-to-end — replaced standalone
> `←` with `[SET]`, fixed CONSTRAINT arity, downgraded `decision_matrix.hlf` v4→v3.

---

## 🔵 Priority 3: GitHub Issue #14 — GUI Cognitive SOC

*Backend data sources are ready; this is GUI-only work*

- [ ] Transparency Panel — InsAIts prose rendering, routing traces, memory tier snapshot
- [ ] Registry Management — sync button, model inventory viewer
- [ ] Feedback UI — thumbs up/down per response, `model_feedback` table
- [ ] Advanced SOC panels (stretch): KYA Provenance Cards, A2A Traffic Light, MITRE overlay

---

## 🟡 Priority 4: Phase 5.3 — HLF Language Developer Experience

- [x] Language Server Protocol (`hlflsp` via `pygls`) — 29 tests
- [x] HLF REPL (`hlfsh`) — 27 tests
- [x] Package Manager (`hlfpm`) with OCI integration — 27 tests
- [x] Test Harness (`hlftest`) + pytest plugin — 23 tests
- [x] MkDocs documentation site auto-generated from `dictionary.json` + `hls.yaml` ✅
- [x] Jules PR #22 sync — `_exec_tool` kwargs expansion fix (194 tests green)

---

## ⬜ Priority 5: SAFE Architecture Tier 1 Backfill

- [ ] MAESTRO Intent Classification system
- [ ] Architecture Decision Record (ADR) system
- [ ] InsAIts V2 daemon (continuous, not just compile-time)
- [ ] SPIFFE/SPIRE upgrade (replace self-signed KYA certs)

---

## ⬜ Priority 6: SAFE Architecture Tier 2 Backfill

- [ ] Z3 Formal Verification integration
- [ ] ALIGN Live Ledger (real-time rule editing with human approval)
- [ ] Full ALS Schema enforcement

---

## ⬜ Priority 7: Research & Experimental

- [ ] HLF-Anchored Memory Nodes — memory segments tagged with HLF intent provenance
- [ ] EGL (Evolutionary Generality Loss) monitoring pipeline — DGM, MAP-Elites, Yunjue
- [ ] Hieroglyphic paper reference integration — Gardiner sign-list taxonomy mapping
- [ ] Soft Veto Gate for near-boundary ALIGN decisions

---

## ⬜ Stretch Goals

- [ ] GitHub Issue #51 — LOLLMS Integration (blocked by #17 and #14)
- [ ] Refactor Project Janus Integration — launch as independent subprocess via `gui/tray_manager.py`
- [ ] Investigate `copilot_changes.diff` for partially landed features
- [ ] Phase 5.2 completion — Wasm sandbox (Wasmtime), Dapr gRPC runtime, `.hlb` binary format
