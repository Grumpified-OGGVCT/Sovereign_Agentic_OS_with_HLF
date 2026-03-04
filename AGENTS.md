# AGENTS.md — Agent Contextual Guide

> This file helps ALL agents (Jules, Copilot, Antigravity, Gemini CLI, etc.) understand the Sovereign Agentic OS with HLF repository.
> **Grammar Version: v0.4.0** | **Last Updated: 2026-03-01** | **RFC: 9005 v3.0 + 9007**

## Architecture Overview

This is a **Sovereign Agentic OS** — a multi-layer AI operating system built around the
**Hieroglyphic Language Format (HLF)**, a Turing-complete mathematical proof language for AI-to-AI communication.

| Layer | Name | Key Files |
|-------|------|-----------|
| 1 | Physical / ACFS | `acfs.manifest.yaml`, `docker-compose.yml`, `security/seccomp.json` |
| 2 | Kernel / Identity | `agents/core/memory_scribe.py`, `agents/core/dream_state.py` |
| 3 | Service Bus | `agents/gateway/bus.py` (FastAPI), `agents/gateway/router.py` (MoMA Router) |
| 4 | Logic / HLF | `hlf/hlfc.py` (Lark parser), `hlf/hlffmt.py`, `hlf/hlflint.py` |
| 5 | Data / Registry | `agents/core/db.py` (SQLite registry), `data/registry.db` |
| 6 | Governance | `agents/gateway/sentinel_gate.py`, `governance/ALIGN_LEDGER.yaml` |
| 7 | Observability | `agents/core/logger.py` (ALS with Merkle chains) |

## 🎩 12-Hat Aegis-Nexus CoVE v2.0 (MANDATORY)

> **CRITICAL**: When decomposing tasks, agents MUST use these 12-Hat personas instead of
> generic agent types. Organize all work, commits, and PR descriptions by hat category.
> **Never skip** ⚫ Black, 🔵 Blue, or 🟪 Purple on any PR — they are mandatory.

All agents MUST structure work using the Sovereign OS's **12-Hat Aegis-Nexus CoVE v2.0** methodology.
Each hat is a specialized adversarial lens integrating full-stack QA validation. You validate across
**14 dimensions**: Functional Correctness, Security Posture (Zero Trust), Data Integrity, AI Safety &
Alignment, Accessibility, Performance Under Duress, Resilience/Anti-Fragility, Regulatory Compliance,
Internationalization, Observability, Infrastructure Hardening, Supply Chain Provenance, **MCP Workflow
Integrity**, and **O(1) Context Bounding**.

---

### 0️⃣ CTX-Budget Protocol (Dynamic Scaling)

```yaml
TIERS:
  base:                          # Local Ollama / 4K models
    max_tokens: 4096
    hat_definitions: 800
    code_diff_workspace: 2048
    json_output: 600
    safety_buffer: 548
    circuit_breaker: 1200        # Truncate evidence above this

  standard:                      # Mid-range models (8K-32K context)
    max_tokens: 32768
    hat_definitions: 4000
    code_diff_workspace: 16384
    json_output: 4000
    safety_buffer: 8384

  extended:                      # Strong agents (128K+ context)
    max_tokens: 131072
    hat_definitions: 12000
    code_diff_workspace: 65536
    json_output: 16000
    safety_buffer: 37536

  sovereign:                     # Infinite CTX / 1M+ models
    max_tokens: dynamic          # Agent self-regulates
    hat_definitions: full
    code_diff_workspace: full
    json_output: unbounded
    constraint: O(1) bounding    # Workspace snapshot + last ≤10 actions

TIER_SELECTION: >
  Agents auto-detect their tier based on available context window.
  Strong agents (Antigravity, Gemini, Jules, etc.) operate at extended
  or sovereign tier and dynamically increase allocation as task
  complexity demands. No agent should be artificially constrained
  below its capability.

GAS_METERING: >
  Regardless of tier, all agents track token consumption per hat
  and report in the final sign-off. This enables cost monitoring
  without imposing artificial limits on capable agents.
```

---

### 1️⃣ Meta-Hat Router (Deterministic Selection)

**Type**: Regex-based pre-processor — **zero LLM tokens consumed**.

```
IF diff_matches(/auth|security|crypto|secret|jwt|oauth|password/i)     → ACTIVATE(⚫ Black)
IF diff_matches(/mcp|tool|server|context7|sequential|workflow/i)       → ACTIVATE(🔷 Azure)
IF diff_matches(/docker|k8s|.tf|.yaml|.yml|infra|deploy/i)            → ACTIVATE(🟠 Orange)
IF diff_matches(/test|spec|.test.|.spec.|coverage/i)                  → ACTIVATE(🟡 Yellow)
IF diff_matches(/prompt|llm|model|embedding|rag|agent|ai/i)           → ACTIVATE(🩵 Cyan)
IF diff_matches(/frontend|ui|component|jsx|tsx|css|a11y/i)            → ACTIVATE(🔴 Red)
IF diff_matches(/i18n|locale|translation|rtl|utf/i)                   → ACTIVATE(🟢 Green)
IF diff_matches(/ci|cd|pipeline|github|workflow|action/i)             → ACTIVATE(🔵 Blue)
IF diff_matches(/cost|token|budget|cache|optimize|performance/i)      → ACTIVATE(🪨 Silver)
IF diff_matches(/license|sbom|spdx|copyright|governance/i)            → ACTIVATE(🟠 Orange)
IF diff_matches(/bias|fairness|demographic|disparity|equity/i)        → ACTIVATE(🩵 Cyan)
ELSE → ACTIVATE(⚫ Black, 🔵 Blue, 🟪 Purple)  # Mandatory minimum
```

🟣 **Meta-Hat Self-Check**: Before execution, verify `{⚫ Black, 🔵 Blue, 🟪 Purple} ⊆ ACTIVE_HATS`.
If missing, abort: `"❗ Missing mandatory hat(s): [list]. Include before proceeding."`

---

### 🔴 Red Hat — Fail-States, Chaos & Resilience

**When to apply**: Code touches error handling, exception paths, database operations, service boundaries, retry logic, shared state, async operations, user-facing interactions, or any component with external dependencies.

**Validation Dimensions**: Functional Correctness, Resilience/Anti-Fragility, Accessibility (Perceptual & Cognitive), UX Integrity

#### Fail-State Analysis
- Cascading failures, service crashes, database locking, single points of failure, race conditions
- Off-by-one errors, missing null checks, unhandled promise rejections, incorrect async/await patterns
- Trace every user gesture (click, tap, swipe, pinch, keyboard shortcut, voice command, hover, focus) → event handler → state mutation → API dispatch → optimistic update → confirmation/reversal
- Verify all form inputs have validation + error states
- Check for: dead clicks, missing loading states, orphaned modals, unhandled empty states

#### Frontend Deep Validation
- **Interaction Integrity**: Trace every button → action → result chain end-to-end
- **Form Logic & Validation Matrix**: Verify client-side validation aligns with server-side validation. Check for: validation bypass via JS disabling, regex denial-of-service (ReDoS), race conditions between blur validation and submit actions
- **Async State Management**: Verify loading skeletons for every async operation, error boundary coverage, empty states for zero-data scenarios, skeleton screen accessibility (aria-busy), retry mechanisms with exponential backoff
- **Frontend Security Surface**: DOM XSS via innerHTML/dangerouslySetInnerHTML, prototype pollution in JSON parsing, postMessage origin validation, localStorage/sessionStorage exposure of sensitive tokens, client-side secret leakage in bundled JS, CSP bypass vectors
- **Responsive & Adaptive**: Verify fluid typography (clamp/rem), container queries, touch target sizes (minimum 44x44px), hover-capable device detection, reduced motion preferences (prefers-reduced-motion), dark mode contrast preservation
- **Performance Budgets**: Core Web Vitals (LCP < 2.5s, INP < 200ms, CLS < 0.1), bundle size limits, third-party script impact, image optimization (WebP/AVIF), font loading strategies (FOUT/FOIT prevention)
- **Progressive Enhancement**: Functionality without JavaScript, SSR hydration mismatch detection, streaming HTML (Suspense boundaries)

#### Chaos Engineering Checks
- **Dependency Failure Simulation**: Database unavailable (fallback to cache?), AI provider timeout (degraded mode?), third-party API 500 (circuit breaker triggers?), CDN failure (origin pull?)
- **Resource Exhaustion**: 100x traffic (autoscaling?), disk full (graceful degradation?), memory pressure (OOM handling?), thread pool exhaustion (queue management?)
- **Network Partitions**: Split-brain scenarios, partition tolerance, gossip protocol failures
- **Byzantine Failures**: Corrupted data from "trusted" sources, clock skew (NTP failure), TLS cert expiration mid-operation
- **Environmental Sabotage**: Internet loss mid-upload, airplane mode during payment, system clock manipulation, JavaScript disabled after page load

#### Accessibility & Inclusive Design (WCAG 2.2 & Beyond)
- **Perceptual**: WCAG 2.2 AA (4.5:1 contrast for normal text, 3:1 for large text, 3:1 for UI components), reflow at 320px, text spacing adaptation (line height 1.5, letter spacing 0.12em), color independence
- **Motor & Interaction**: Full keyboard operability, focus indicators (minimum 2px outline), focus trap management in modals, skip links, accessible authentication (CAPTCHA alternatives)
- **Cognitive**: Consistent navigation, error prevention for destructive actions, readable text levels (Flesch scores), extended time limits, distraction reduction (autoplay controls)
- **Screen Reader**: Semantic HTML (landmarks, headings hierarchy), ARIA live regions for dynamic content, alternative text for complex images, form labeling, status message announcements
- **Assistive Tech**: Speech input compatibility, switch navigation, screen magnification (200%+), high contrast mode (forced colors media query)

**Flag**: Z-index nightmares, focus trap escapes, memory leaks in event listeners, hydration mismatches, viewport locking on iOS Safari, no graceful degradation, cascading failure potential, missing bulkhead isolation, missing skip links, inaccessible dropdowns, missing alt text, empty links/buttons, improper heading hierarchy, missing page titles, autoplaying audio without pause, form errors without programmatic association

---

### ⚫ Black Hat — Security Exploits & Zero Trust Compliance

**When to apply**: ALWAYS on PRs. Code touches auth, user input, file I/O, network calls, config, agent operations, encryption, secrets management, or any external-facing surface.

**Validation Dimensions**: Security Posture (Zero Trust), Supply Chain Provenance, Regulatory Compliance

#### OWASP Top 10 2025
- **A01** Broken Access Control: Privilege escalation, IDOR, missing authorization, CORS with wildcard+credentials
- **A02** Cryptographic Failures: TLS 1.3, cert pinning, key rotation, algorithm deprecation (MD5/SHA1/RC4), HSM, E2E encryption
- **A03** Injection: SQL (even ORMs with raw SQL), XSS (DOM/stored/reflected), LDAP, template, command injection
- **A04** Insecure Design: Missing threat modeling, business logic flaws, missing rate limiting
- **A05** Misconfiguration: Default credentials, unnecessary features, missing security headers, verbose errors
- **A06** Vulnerable Components: Outdated deps with CVEs, transitive dependency risks
- **A07** Auth Failures: OAuth 2.1/PKCE, JWT (algorithm confusion, none algorithm, key rotation), refresh token rotation, session fixation
- **A08** Integrity Failures: Unsigned updates, unverified CI/CD, dependency confusion attacks
- **A09** Logging Failures: Missing audit trails, PII in logs, insufficient monitoring
- **A10** SSRF: Server-side request forgery via URL parsers, DNS rebinding

#### Supply Chain Security
- SBOM completeness, SLSA provenance, signed container images
- Dependency pinning with hash verification, private registry auth
- Typosquatting protection, post-quantum readiness (PQC migration planning)

#### Identity & Access
- OAuth 2.1/PKCE, JWT security (algorithm confusion, none algorithm bypass, key rotation)
- Refresh token rotation, scope validation, RBAC/ABAC enforcement
- Privilege escalation paths, session fixation prevention

#### Privacy Engineering
- Data minimization, purpose limitation, consent management
- Right-to-erasure automation (GDPR Article 17), cascade deletion
- Data portability export, cross-border transfers (SCCs), data anonymization for non-prod (GDPR Article 32)

#### Adversarial User Security Tests
- Cryptominer exploitation (resource exhaustion), scraper bypass (rotating proxies), social engineering via UI (homograph attacks), compliance evasion (log tampering, steganography)
- **Input Fuzzing**: 10MB text in single-line inputs, SQL/LaTeX/Markdown injection, polyglot files (valid JPG+PHP), zero-width joiners, RTL overrides
- **Concurrency Attacks**: Rapid duplicate clicks, multi-tab form submission, race condition exploitation
- **Business Logic Abuse**: Stacked discounts, negative quantities, client-side price manipulation, IDOR via sequential IDs

**Flag**: Hardcoded credentials (password=, api_key, AWS keys), missing CSP, clickjacking (X-Frame-Options), insecure deserialization, SSRF, GraphQL depth limits missing, API key rotation absent, missing request ID propagation, any input without sanitization, exposed secrets, missing rate limiting

---

### ⚪ White Hat — Efficiency, Performance & Data Integrity

**When to apply**: Code touches LLM calls, database queries, loops, data processing, memory-heavy operations, file I/O, caching, or resource-intensive operations.

**Validation Dimensions**: Performance Under Duress, Data Integrity

#### Performance & Resource Analysis
- Token waste, gas budgets, unnecessary LLM calls, context sizes, DB bloat, memory leaks
- N+1 queries, missing indexes, full table scans, unbounded queries (missing LIMIT), connection pool exhaustion
- Large file handling at size limits, empty/max-length/special-character/emoji inputs
- Missing pagination, no debouncing, missing cleanup on unmount

#### Data Integrity
- **Transactions**: ACID compliance, saga patterns, two-phase commit, eventual consistency reconciliation
- **Migration Safety**: Expand/contract pattern, rollback procedures, data loss prevention, table locking risks
- **Data Validation**: Check constraints, foreign key enforcement, unique constraint races, data type overflows (2038 timestamp, integer overflows)
- **Backup & Recovery**: RPO/RTO testing, point-in-time recovery, cross-region replication lag

**Flag**: SQL injection via dynamic queries, read-modify-write race conditions, missing pessimistic locking for financial operations, unencrypted PII at rest, timezone inconsistencies

---

### 🟡 Yellow Hat — Synergies, Optimization & Strategic Value

**When to apply**: Code adds new features or modifies existing components. Also applied when reviewing overall system value delivery.

**Validation Dimensions**: Strategic Value, Missed Opportunities

#### Synergy Analysis
- Cross-component synergies, hidden powers, 10x improvements, reuse opportunities
- Shared utility extraction, API surface area optimization

#### Missed Opportunities
1. **Feature/Architecture**: Capabilities differentiating product or reducing operational cost by 20%+
2. **AI Enhancement**: Untapped AI capabilities (anomaly detection, personalization, predictive caching)
3. **Operational Excellence**: Observability/automation improvements reducing MTTR by 50%
4. **Developer Experience**: Tools/abstractions accelerating development velocity
5. **User Experience**: UX patterns measurably improving engagement/retention

**Flag**: Duplicated logic, missed caching opportunities, manual processes ripe for automation, underutilized infrastructure

---

### 🟢 Green Hat — Evolution, Missing Mechanisms & Feature Completeness

**When to apply**: Code adds new capabilities, extends architecture, modifies core systems, or touches growth paths.

**Validation Dimensions**: Feature Completeness, Growth Readiness, Internationalization

#### Evolution Readiness
- Missing operational wiring, growth paths, emergent behaviors
- Dead Code Excavation: tree-shaking failures, commented-out legacy logic, unused env vars, orphaned DB tables, zombie microservices
- Configuration Drift Detection: dev/staging/prod variances in feature flags, timeouts, resource limits, security headers

#### Loose Wiring / Unfinished Functions
- UI components visible but not wired to backend
- Placeholder content in production ("Coming Soon", Lorem ipsum)
- Feature flags enabled but underlying service is mock/stub
- Documentation gaps: documented endpoints returning 404
- Orphaned code, dead imports, unused variables

#### Internationalization (i18n/l10n)
- **Characters**: UTF-8 throughout, RTL support (Arabic/Hebrew), bidirectional text, CJK font subsetting
- **Content**: Locale-aware date/time/number/currency formatting, pluralization (CLDR), collation/sorting
- **UI Resilience**: German compound words, Japanese vertical text, non-Latin scripts, emoji (Unicode 15.0+)
- **Cultural Safety**: Iconography sensitivity, color symbolism, imagery diversity
- **Localization QA**: Translation key completeness, pseudo-localization testing, missing translation fallbacks

**Flag**: Missing health checks, undefined graceful degradation, circular dependencies, SPOFs without redundancy, secrets in VCS, hardcoded English strings, concatenated untranslatable strings, fixed-width layouts breaking with long translations, DST timezone bugs

---

### 🔵 Blue Hat — Process, Observability & Operational Readiness

**When to apply**: ALWAYS on PRs. Checks internal consistency, documentation, observability, and operational preparedness.

**Validation Dimensions**: Observability, Process Completeness, Operational Readiness

#### System Architecture Mapping
- Map all entry vectors: HTTP/HTTPS, WebSocket, gRPC, GraphQL, message queue consumers, cron, webhooks, serverless triggers, edge functions, CLI interfaces
- **State Archaeology**: Data lineage from UI state → API → Cache → DB → Eventual consistency
- **Dependency Graph**: Internal service deps, external APIs (with SLAs), AI provider failover chains, circuit breaker configs

#### Observability
- **Telemetry**: Distributed tracing (OpenTelemetry), correlation IDs, structured logging (JSON), metric cardinality prevention, log sampling
- **Health & Readiness**: Liveness vs readiness probes, startup probes for slow containers, dependency health aggregation
- **Alerting**: Fatigue prevention (severity classification), runbook links, self-healing for known failures, escalation policies
- **Incident Response**: Feature flag kill switches, circuit breaker dashboards, chaos engineering schedules, postmortem templates

#### Operational Readiness
- Runbooks for every Critical/High finding
- Monitoring dashboards reviewed for alert fatigue
- On-call rotation aware of new features and failure modes
- Rollback procedure tested (restore within RTO)

**Flag**: Missing error tracking (Sentry), PII in logs, unmonitored background queues, DB connection leak detection missing

---

### 🟣 Indigo Hat — Cross-Feature Architecture & Integration

**When to apply**: Code modifies multiple files/components, refactors, adds integration points, or touches API boundaries.

**Validation Dimensions**: Integration Integrity, Contract Compliance

#### API & Integration Contracts
- **Schema Compliance**: Validate against OpenAPI/GraphQL with fuzzing (1000+ malformed requests). Missing required fields, type coercion failures, null handling, unicode normalization
- **HTTP Semantics**: Status codes (401 vs 403, 409, 422), cache-control headers, ETags, Content-Disposition
- **Async & Events**: Webhook delivery guarantees, idempotency keys, out-of-order messages, dead letter queues, poison pills
- **Circuit Breakers**: Timeout configs (connect vs read), retry with jitter, bulkhead isolation, fallback caches
- **Versioning**: Breaking change detection, backward compatibility layers, deprecation headers (Sunset), migration notices

#### Cross-Component Analysis
- Pipeline consolidation, redundant components, macro-level DRY violations, gate fusion
- End-to-end critical user journeys, API contract compliance, async race conditions, file I/O edge cases

**Flag**: Mass assignment vulnerabilities, GraphQL depth limits, missing API key rotation, request ID propagation gaps, CORS misconfigurations

---

### 🩵 Cyan Hat — Innovation, AI/ML Validation, Bias & Feasibility

**When to apply**: Code introduces new patterns, experimental features, technology choices, modifies AI/ML pipelines, or impacts demographic outcomes.

**Validation Dimensions**: AI Safety & Alignment, Innovation Feasibility, AI Bias & Fairness

#### AI/ML Adversarial Validation (2025+ Standards)
- **Prompt Injection**: Direct injection, indirect injection (RAG poisoning), multi-turn jailbreaks, 50+ attack variations. Verify input sanitization, output encoding, instruction hierarchy enforcement
- **RAG Pipeline**: Chunking quality (semantic vs fixed-size), embedding drift, vector DB consistency, context overflow, citation accuracy (grounding), hallucination metrics (faithfulness, relevance)
- **Agent & Tool Safety**: Tool permission scaffolds (least privilege), human-in-the-loop for irreversible actions, loop termination conditions (infinite recursion prevention), tool output validation
- **Model Robustness**: Adversarial input (typos, homoglyphs, obfuscation), model DoS (token exhaustion), training data extraction (memorization), bias amplification
- **Observability & Alignment**: Prompt/response logging (PII redaction), A/B testing infrastructure, guardrails (moderation classifiers), explainability hooks
- **Multi-Modal**: Toxic content detection in uploads, prompt injection via image metadata (EXIF), adversarial patches, audio transcription hallucination
- **Model Fallback**: Primary → secondary → cached → static fallback chain
- **AI-Specific Attacks**: Prompt injection via email, jailbreak via base64/translation, training data poisoning via feedback loops, model extraction

#### AI Bias & Fairness Validation
- **Demographic Test Matrix**: Test outputs across demographic slices (age, gender, ethnicity, disability, socioeconomic status)
- **Disparity Impact Analysis**: Measure outcome disparities; flag statistical deviations exceeding fairness thresholds
- **Mitigation Suggestions**: Re-weighting, debiasing techniques, adversarial training, calibration
- **Representational Harms**: Stereotyping, erasure, denigration in generated content
- **Audit Trail**: Document bias evaluation methodology, thresholds, and remediation steps

**Flag**: Unvalidated AI outputs in SQL generation (NL2SQL), missing rate limiting on AI endpoints (cost explosion), no model fallback (single provider dependency), missing "I don't know" calibration, hallucination risks, bias indicators, untested demographic slices, disparate outcomes without mitigation

---

### 🟪 Purple Hat — AI Safety, Compliance & Regulatory

**When to apply**: ALWAYS on PRs. Code touches agent behavior, LLM prompts, epistemic modifiers, data handling, or regulatory-scoped components.

**Validation Dimensions**: Regulatory Compliance, AI Safety

#### OWASP LLM Top 10 2025
- **LLM01** Prompt Injection (direct/indirect)
- **LLM02** Insecure Output Handling (encode AI outputs before rendering)
- **LLM03** Training Data Poisoning (provenance verification)
- **LLM04** Model DoS (token limit exhaustion prevention)
- **LLM05** Supply Chain (model provenance, weight integrity)
- **LLM06** Sensitive Data Disclosure (PII in responses)
- **LLM07** Insecure Plugin Design (tool permission scaffolds)
- **LLM08** Excessive Agency (unbounded agent capabilities)
- **LLM09** Overreliance (missing human-in-the-loop for high-stakes)
- **LLM10** Model Theft (inference API security, rate limiting)

#### Sovereign OS Specific — ALIGN Rules & Epistemic Safety
- ALIGN rules (R-001 to R-008+): Regex-based safety gates
- Epistemic modifiers [BELIEVE]/[DOUBT]: Must not affect security decisions
- Gas metering: Resource budgets for agent operations
- ACFS: Sandboxed file access
- Deployment tiers: hearth → forge → sovereign
- Host functions: READ, WRITE, SPAWN, WEB_SEARCH — tiered capabilities

#### Compliance Matrix
- **EU AI Act (2024/1689)**: High-risk registration, CE marking, post-market monitoring
- **GDPR**: DPIA, data subject rights, Article 32 security
- **CCPA**: Retention, consent, deletion automation
- **NIST AI RMF 1.0**: Govern/Map/Measure/Manage coverage
- **ISO 42001**: AI Management Systems
- **NIS2/DORA, PCI-DSS, HIPAA, SOC 2, ISO 27001**: As applicable

**Flag**: ALIGN rule bypass via epistemic modifier abuse, PII leakage, missing consent, unaudited AI decisions, regulatory violations > €10M fines

---

### 🟠 Orange Hat — DevOps, Infrastructure, License & Governance

**When to apply**: Code touches CI/CD, Docker, deployment configs, scripts, Git workflows, IaC, operational infrastructure, licensing, or dependency governance.

**Validation Dimensions**: Infrastructure Hardening, Supply Chain Provenance, License Compliance

#### Container Security
- Non-root execution, read-only root filesystems, distroless base images
- CVE scanning (no CRITICAL unpatched), secret mounting (tmpfs/encrypted), resource limits

#### Kubernetes
- Pod security policies (OPA/Gatekeeper), network policies (zero-trust), pod disruption budgets
- Secrets management (external-secrets/Vault), ingress TLS termination

#### IaC Validation
- Terraform state encryption, drift detection, plan review gates, cost thresholds, resource tagging

#### CI/CD Pipeline Security
- Artifact signing, SLSA Level 3+, hermetic builds, secret scanning (gitleaks), branch protection (signed commits), production approval gates

#### License & Governance
- **SBOM Completeness**: SPDX or CycloneDX format, all direct + transitive deps inventoried
- **Prohibited License Detection**: GPL/AGPL/SSPL in proprietary contexts, copyleft contamination
- **Copyleft Conflicts**: Identify copyleft-to-permissive dependency chains
- **SLSA Provenance Verification**: Build attestations, artifact signatures
- **Attribution Requirements**: License notice files, copyright headers

**Flag**: Docker socket mounting, privileged containers, missing pod security contexts, hardcoded cloud credentials in IaC, public S3 buckets, missing SBOM, prohibited licenses in dependency tree, unsigned artifacts

---

### 🪨 Silver Hat — Context, Token & Resource Optimization + O(1) Bounding

**When to apply**: Code touches prompt construction, context building, token-sensitive operations, resource budgets, or context management systems.

**Validation Dimensions**: Token Optimization, Cost Efficiency, O(1) Context Bounding

#### Token & Context Analysis
- Token budgets, gas formula efficiency, context window utilization, prompt compression
- LLM call deduplication, response caching, embedding computation efficiency
- Vector DB query optimization, batch vs real-time trade-offs
- Cost-per-query projections and budget guardrails

#### O(1) Context Bounding (Infinite CTX Protocol)

**Validation Checklist**:
- [ ] **Bounded Prompt Construction**: Workspace snapshot (hashes) + last ≤10 actions only
- [ ] **No Linear Accumulation**: Historical tokens actively discarded, not truncated. Context size remains constant regardless of task length
- [ ] **Snapshot Integrity**: Deterministic state capture using file hashes, not full content
- [ ] **Global-Reasoning Threshold**: 10M+ token contexts only when explicitly justified
- [ ] **Gas Metering Enforcement**: Resource budgets enforced per Sovereign OS constraints
- [ ] **Reconstruction Fidelity**: Bounded prompt must reconstruct sufficient context for correct decision-making

**Flag**: Unbounded context growth, linear token accumulation, missing O(1) reconstruction, snapshot non-determinism, missing gas metering, context window overflow without circuit breaker

---

### 🔷 Azure Hat — MCP Workflow Integrity *(NEW in v2.0)*

**When to apply**: Code touches MCP server definitions, tool schemas, agent loops, task management, or any component in the MCP orchestration pipeline.

**Validation Dimensions**: MCP Workflow Integrity, Agent Lifecycle Compliance

#### Task Lifecycle Compliance
- [ ] **Sequencing**: Verify `request_planning` → `approve_task_completion` → `get_next_task` sequencing is enforced
- [ ] **State Machine Enforcement**: Task states must follow defined transitions. No skip-ahead, no backwards transitions without rollback
- [ ] **Deadlock Prevention**: Identify patterns where agents wait indefinitely for user approval

#### Context7 Usage Validation
- [ ] Deep retrieval calls respect bounded context policy (snapshot + last 10 actions per 🪨 Silver Hat)
- [ ] Context7 queries include scope limiting (library, version, topic constraints)
- [ ] Retrieved context is validated before injection into agent prompts

#### Sequential Thinking Enforcement
- [ ] Structured step-IDs present in all multi-step operations
- [ ] No unbounded branching (fork without join)
- [ ] Step dependencies explicitly declared

#### HITL (Human-in-the-Loop) Gates
- [ ] Irreversible actions require explicit user confirmation
- [ ] Escalation paths defined for ambiguous decisions
- [ ] Timeout handling for pending human approvals

#### Tool Justification Protocol
- [ ] Every MCP tool call includes inline rationale
- [ ] Tool selection is proportional to task complexity
- [ ] Tool output validation before downstream consumption

**Flag**: Missing approval gates, unverified task transitions, MCP calls without justification, missing HITL for destructive operations, deadlock-prone agent loops, missing step-IDs

---

### Upkeep Responsibilities — GUI, Demo Page, README, Setup & Auto-Update

#### 🔴 Red Hat — Demo Page Functional Integrity
- After ANY code change, verify `docs/index.html` still functions. No mocks — all demo page functionality must use real endpoints.

#### 🔵 Blue Hat — Documentation Sync (GUI, Demo, README)
- New MCP tools must appear in the demo page. README must reflect current setup/install instructions, architecture, features, and dependencies. No stale sections.

#### 🟢 Green Hat — Feature Parity & Completeness
- Audit for feature drift between backend capabilities and demo page/GUI. No orphaned UI. No undocumented features.

#### 🟣 Indigo Hat — Integration Wiring, Setup & Auto-Update
- API contract changes must propagate to frontend fetch calls. `install.bat`/`install.sh`, `setup_wizard.py`, `run.bat`/`run.sh` must work end-to-end. Auto-update checker must remain functional.

#### 🔷 Azure Hat — MCP Pipeline Upkeep
- Task lifecycle sequencing must not be broken. HITL gates must remain wired. Sequential thinking step-IDs must propagate.

---

### Review Protocol

1. **Run Meta-Hat Router** — Apply regex router against git diff (zero LLM tokens)
2. **Verify Mandatory Set** — Confirm `{⚫ Black, 🔵 Blue, 🟪 Purple} ⊆ ACTIVE_HATS`
3. **Retrieve Hat Definitions** — Load only activated hats. Budget: ~800 tokens for 2-3 hats
4. **Run Each Hat** — Apply full validation checklist. Severity: 🔴 CRITICAL, 🟠 HIGH, 🟡 MEDIUM, 🟢 LOW
5. **Generate Output** — Produce findings in both Markdown (human) and JSON (CI/CD) formats

### Severity Levels

- 🔴 **CRITICAL** — Must fix before merge. Data loss, RCE, SQLi, AI safety failure, regulatory violation > €10M
- 🟠 **HIGH** — Should fix. 25%+ user impact, interactive XSS, SPOF without failover, unencrypted PII at rest
- 🟡 **MEDIUM** — Fix soon. UX friction, slow queries, incomplete edge case handling
- 🟢 **LOW** — Nice to have. Typos, suboptimal algorithms, missing metrics

### Commit Message Format

```
feat(<hat>): <description>

Example:
feat(black-hat): add path sanitization to READ host function
fix(red-hat): handle SQLite database locked under concurrency
feat(azure-hat): add HITL gate to MCP task completion flow
```

### Anti-Reductionist Mandate

Agents MUST NEVER produce empty "all clean" reviews. If a hat genuinely has no findings,
explain (in 2-3 sentences) what was examined and why it passed. Generic "looks good" is
**prohibited** — this is a military-grade system.

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
5. **Run through Eleven Hats** — Every transparency enhancement MUST pass the Eleven Hats review protocol
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
   - Pass the proposal through Eleven Hats before submitting
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

## HLF Grammar Codex (v0.4.0 — MANDATORY READING)

All agents MUST be aware of the current HLF grammar state before composing or modifying HLF.

### Current Operator Catalog

| Operator | Glyph | Purpose | RFC |
|----------|-------|---------|-----|
| Tool Execution | `↦ τ()` | Execute named tool | 9005 §4.1 |
| Conditional | `⊎ ⇒ ⇌` | If/then/else branching | 9005 §3.2 |
| Negation | `¬` | Logical NOT | 9005 §3.1 |
| Intersection | `∩` | Logical AND | 9005 §3.1 |
| Union | `∪` | Logical OR | 9005 §3.1 |
| Assignment | `←` | Bind value to name | 9005 §5.1 |
| Type Annotation | `:: 𝕊/ℕ/𝔹/𝕁/𝔸` | Declare value type | 9005 §2.3 |
| Parallel | `∥` | Concurrent execution | 9005 §6.1 |
| Sync Barrier | `⋈` | Wait-then-execute | 9005 §6.2 |
| Pass-by-Ref | `&` | Mutable reference | 9005 §5.3 |
| Struct | `≡` | Define typed struct | 9007 §2.1 |
| Epistemic | `_{ρ:val}` | Confidence score | 9005 §7 |
| Glyphs | `⌘ Ж ∇ ⩕ ⨝ Δ ~ §` | Statement modifiers | Core |

Full reference: **`docs/HLF_GRAMMAR_REFERENCE.md`**

### Self-Correcting Feedback Loop (Iterative Intervention Engine)

When an agent sends malformed HLF, the system:
1. Compiles the HLF via `hlfc.compile()`
2. On failure, calls `format_correction(source, error)` to generate structured feedback
3. Returns the correction to the offending agent with:
   - The specific error message
   - The complete valid operator catalog
   - A human-readable explanation
   - A suggestion for how to fix it
4. The agent retries with corrected syntax
5. If still failing after 3 attempts, escalates to human operator

Agents MUST handle `format_correction()` responses and self-adapt.

### InsAIts V2 Transparency Mandate

Every AST node includes a `human_readable` field. Agents MUST:
- Preserve `human_readable` in all downstream processing
- Use it for audit logging and human-facing displays
- Never strip or modify the transparency field

## HLF Agent Maximization

All agents handling HLF components MUST be maximized for usefulness and power.

### Rules

1. **Grammar awareness** — Check `docs/HLF_GRAMMAR_REFERENCE.md` BEFORE composing HLF
2. **Grammar evolution** — Actively propose new HLF tags, syntax extensions, and expressive power improvements
3. **Compiler hardening** — Expand `hlfc.py` test coverage, edge case handling, and error messages
4. **Linter expansion** — Add new lint rules to `hlflint.py` for security, performance, and best practices
5. **Formatter improvements** — Enhance `hlffmt.py` for canonical formatting and readability
6. **Runtime capabilities** — Expand `hlfrun.py` with new built-in functions, action types, and host function bindings
7. **Test corpus growth** — Continuously expand the HLF test corpus with real-world examples, edge cases, and adversarial inputs
8. **Documentation** — Keep `docs/HLF_GRAMMAR_REFERENCE.md` current with every grammar change
9. **Error feedback** — Use `format_correction()` for self-correcting feedback when compilation fails
10. **Never simplify** — HLF changes MUST be additive. Never remove syntax, reduce expressiveness, or simplify existing capabilities.

## Anti-Reduction Checklist (MANDATORY for every Jules PR)

> **PR Completion Gate** — every item below MUST be checked before a PR may be merged.
> A PR that has not passed the CoVE audit (compact or full) **must not be merged**.

- [ ] No files deleted
- [ ] No tests removed or weakened
- [ ] No features simplified or scope-reduced
- [ ] No transparency features hidden or removed
- [ ] No governance rules weakened
- [ ] No model capabilities reduced
- [ ] Coverage >= baseline
- [ ] Test count >= baseline
- [ ] Eleven Hats review completed
- [ ] **CoVE audit passed (compact or full)** — run `scripts/verify_chain.py --cove` and attach output

