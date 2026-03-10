# Unified Ecosystem Roadmap

> Created: 2026-03-10 — Sovereign Agentic OS Session

## Vision

All external AI apps (LOLLMS, MSTY Studio, AnythingLLM, Jan.ai, etc.) and all user repos integrate through **HLF host functions** — the same `CALL_HOST` opcode, the same 6-gate pipeline, the same Merkle-chain audit trail that governs everything else in the OS.

External apps are **not** plugins, adapters, or browser embeddings. They are HLF host functions registered in the `HostFunctionRegistry`, dispatched through `agents/core/host_function_dispatcher.py`, and governed by the full LALR(1) → 6-gate → bytecode VM → Scribe/Sentinel audit pipeline.

## User Repo Integration Map

| Repo | OS Role | HLF Host Functions |
|------|---------|-------------- ------|
| **Project Janus** | RAG pipeline input (archival) | `janus.crawl`, `janus.query`, `janus.archive` |
| **OVERWATCH** | Sentinel process watchdog | `overwatch.scan`, `overwatch.terminate`, `overwatch.status` |
| **API-Keeper** | Credential vault persistence | `apikeeper.store`, `apikeeper.rotate`, `apikeeper.audit` |
| **Jules_Choice** | Autonomous coding agent | `jules.spawn_session`, `jules.execute_sdd` |
| **ChronosGraph** | Video transcription → RAG | `chronos.transcribe`, `chronos.ingest` |
| **ollama_pulse** | `CLOUD_CATALOG` auto-updater | `pulse.scan`, `pulse.update_catalog` |
| **MoE Endpoint** | Advanced gateway routing | `moe.route`, `moe.ensemble` |
| **SearXng_MCP** | Private search backend | `searxng.search`, `searxng.crawl` |
| **BrowserOS_Guides** | Knowledge base builder → RAG | `browserguides.extract`, `browserguides.compile` |
| **Credential-Locator** | API-Keeper complement | `credlocator.scan`, `credlocator.report` |

## External App Integration Map

> **Note:** All API endpoints listed below are **defaults** — actual values are configurable via `.env` → `config/settings.json`.

| App | HLF Host Functions | API Endpoint (default) |
|-----|-------------------|-------------|
| **LOLLMS** | `lollms.generate`, `lollms.rag_query`, `lollms.list_models`, `lollms.run_skill` | `http://localhost:9600` |
| **MSTY Studio** | `msty.knowledge_query`, `msty.split_chat`, `msty.persona_run` | Local storage / API |
| **AnythingLLM** | `anythingllm.workspace_query`, `anythingllm.agent_flow`, `anythingllm.list_docs` | `http://localhost:3001/api/v1/` |
| **Jan.ai**¹ | `jan.generate`, `jan.list_models` | `http://localhost:1337` |

¹ *Note: `jan.*` refers to Jan.ai; `janus.*` refers to Project Janus. Namespaces are distinct — no conflict.*

## Phased Build Sequence

### Phase 1: Foundation
- [ ] Register external app HLF host functions in `HostFunctionRegistry`
- [ ] Wire API-Keeper as credential vault persistence for `agents/core/model_gateway.py`
- [ ] Wire SearXng_MCP as `WEB_SEARCH` backend in `agents/core/host_function_dispatcher.py`

### Phase 2: RAG + Data
- [ ] Build Hybrid RAG pipeline (BM25 + vector + cross-encoder reranker)
- [ ] Wire Janus → RAG, ChronosGraph → RAG, BrowserOS_Guides → RAG
- [ ] Import external RAG from LOLLMS DataStores / MSTY Knowledge Stacks / AnythingLLM

### Phase 3: Intelligence
- [ ] Structured outputs (Pydantic + Instructor) wrapping Model Gateway
- [ ] MoE routing merge from moe-ollama-endpoint into `agents/core/model_gateway.py`
- [ ] ollama_pulse → `CLOUD_CATALOG` auto-sync

### Phase 4: Autonomy
- [ ] POMDP lifecycle upgrades (belief states, pre-act safety projection)
- [ ] Jules_Choice as Devin-style agent with self-healing retry loop
- [ ] Tree-sitter AST for code understanding

### Phase 5: Hardening
- [ ] OVERWATCH → Sentinel process watchdog integration
- [ ] Multi-agent heartbeat monitoring + confidence-based routing
- [ ] Credential-Locator → API-Keeper lifecycle chain
- [ ] ModelTron → performance feedback loop for routing decisions

## Research Sources

- Exa deep-reasoning search (requestId: `40a17ed612a87e716eeca479ae9cf1a5`) — 18 citations
- arXiv:2602.23720 (Auton POMDP framework)
- arXiv:2603.01327 (Devin-style coding agent)
- arXiv:2602.10479 (Multi-agent hardening)
- ParisNeo LOLLMS (daily commits Mar 4-9, 2026)
- MSTY Studio architecture (Knowledge Stacks, Shadow Persona, Split Chats)
- AnythingLLM architecture (workspace RAG, no-code agent builder, Developer API)
