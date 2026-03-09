# Sovereign Agentic OS — Master TODO

> Last updated: 2026-03-08 | Test baseline: 1,075 passing (100%)
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

---

## 🔴 Priority 1: Phase 5.1 Completion — OCI Module Distribution

*Remaining ~10% of Phase 5.1*

- [ ] OCI module distribution (`docker push` for `.hlf` modules)
- [ ] `[IMPORT]` tag resolution via OCI registry paths in `config/settings.json`
- [ ] `acfs.manifest.yaml` `modules:` section for checksum tracking

---

## 🟠 Priority 2: GitHub Issue #17 — Aegis-Nexus Sentinel/Scribe/Arbiter

*Runtime daemons — the unique value beyond 14-Hat review*

- [ ] Sentinel runtime daemon — background privilege escalation / injection anomaly monitor
- [ ] Scribe runtime daemon — continuous InsAIts translation stream + 80% token budget gate
- [ ] Arbiter runtime daemon — inter-agent dispute resolution via ALIGN adjudication
- [ ] ASB Redis Streams inter-agent communication wiring
- [ ] Per-agent gas accounting integration
- [ ] Agent profile updates in `config/personas/`

---

## 🔵 Priority 3: GitHub Issue #14 — GUI Cognitive SOC

*Backend data sources are ready; this is GUI-only work*

- [ ] Transparency Panel — InsAIts prose rendering, routing traces, memory tier snapshot
- [ ] Registry Management — sync button, model inventory viewer
- [ ] Feedback UI — thumbs up/down per response, `model_feedback` table
- [ ] Advanced SOC panels (stretch): KYA Provenance Cards, A2A Traffic Light, MITRE overlay

---

## 🟡 Priority 4: Phase 5.3 — HLF Language Developer Experience

- [ ] Language Server Protocol (`hlflsp` via `pygls`)
- [ ] HLF REPL (`hlfsh`)
- [ ] Package Manager (`hlfpm`) with OCI integration
- [ ] Test Harness (`hlf-test`) + pytest plugin
- [ ] MkDocs documentation site auto-generated from `dictionary.json` + `hls.yaml`

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
