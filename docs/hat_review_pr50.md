## 🎩 11-Hat Aegis-Nexus Automated PR Review

**PR #50**: PR #50 (offline review)
**Author**: @jules[bot]
**Mergeable**: unknown

---

### ⚫ Black Hat — Security Exploits
**Focus**: Prompt injection, ALIGN bypass, data exfiltration, privilege escalation

**Findings**: 8 (🔴 CRITICAL: 4, 🟠 HIGH: 2, 🟡 MEDIUM: 2)

<details>
<summary>🔴 <b>[CRITICAL]</b> Arbiter Agent Template Grants Sovereign-Tier SPAWN Capability to Hearth Deployment</summary>

**Description**: The `seed_aegis_templates()` function in `agents/core/db.py` inserts an 'arbiter' agent template with `tools: ["READ", "WRITE", "SPAWN"]`. According to `host_functions.json`, SPAWN is restricted to `["forge","sovereign"]` tiers. However, the system is configured for `deployment_tier: hearth` (lowest tier), creating a privilege escalation path where hearth-tier code can instantiate agents with sovereign-tier container spawning capabilities, bypassing tier isolation.

**Recommendation**: Remove SPAWN from the arbiter template or gate template seeding behind tier checks. Implement runtime validation in `host_function_dispatcher.py` to verify agent tier against function tier at invocation time, rejecting calls where agent tier < function tier.

</details>

<details>
<summary>🔴 <b>[CRITICAL]</b> Disabled TLS and Vault Expose Unencrypted Credential Flows</summary>

**Description**: Settings explicitly disable security controls: `"enable_mtls": false`, `"enable_vault": false`. Host functions WEB_SEARCH and HTTP_GET can transmit sensitive data over unencrypted channels. Combined with R-004 regex block for env exfiltration (bypassable via encoding), this allows plaintext transmission of credentials and sensitive data without cryptographic protection.

**Recommendation**: Enable mTLS for all inter-service communication (`enable_mtls: true`), enable Vault integration for secret management (`enable_vault: true`), and mandate TLS 1.3 for all HTTP_GET/WEB_SEARCH invocations via the Dapr HTTP proxy configuration.

</details>

<details>
<summary>🔴 <b>[CRITICAL]</b> HLF Runtime OpenClaw Operation Bypasses ALIGN Regex Block</summary>

**Description**: ALIGN rule R-008 blocks `regex_block: 'openclaw:'` to prevent raw OpenClaw usage. However, `hlf/runtime.py` adds handling for `elif op == "openclaw":`, executing this operation after ALIGN processing. The ALIGN regex requires a trailing colon (`openclaw:`) but the runtime likely uses exact matching (`op == "openclaw"`), allowing bypass via the operation without colon. Additionally, if HLF executes compiled bytecode, the ALIGN string-based regex never matches the operation code.

**Recommendation**: Normalize operation identifiers before ALIGN validation (lowercase, exact match without relying on regex colons). Implement ALIGN policy checks at the HLF runtime level, verifying operation codes against a denylist before execution.

</details>

<details>
<summary>🔴 <b>[CRITICAL]</b> Dynamic Tool Loading from /data/tool_forge Without Signature Verification</summary>

**Description**: The PR adds `/data/tool_forge` directory (permissions 700) in `acfs.manifest.yaml` and implements `ToolForge` class in `agents/core/tool_forge.py` with `register_tool()` accepting arbitrary callables. While `acfs.manifest.yaml` declares a module hash, no verification logic is visible in the tool loading path. An attacker can write malicious Python modules to `/data/tool_forge` achieving arbitrary code execution if dynamic loading is implemented without hash verification.

**Recommendation**: Implement mandatory SHA-256 verification for all tools/modules loaded from `/data/tool_forge` against `acfs.manifest.yaml` checksums before execution. Use `importlib.util` with pre-execution hash validation, rejecting any module not matching the manifest.

</details>

<details>
<summary>🟠 <b>[HIGH]</b> Sentinel Agent Template Enables Covert Data Exfiltration via WEB_SEARCH</summary>

**Description**: The Sentinel template includes `WEB_SEARCH` tool (marked `sensitive: true`) with `allow_network: True`. An attacker controlling agent inputs can encode sensitive data (file contents, environment variables) from READ operations into search query strings, exfiltrating data through search engine query logs. ALIGN R-004 blocks explicit `.env` patterns but cannot detect base64/hex encoded data in search queries.

**Recommendation**: Remove WEB_SEARCH from the Sentinel template; security scanning should not require external network access. If network access is mandatory, implement a sanitizing proxy that blocks high-entropy queries and restricts URLs to a strict allowlist, preventing data encoding in query parameters.

</details>

<details>
<summary>🟠 <b>[HIGH]</b> Unsanitized Path Arguments Enable Arbitrary File Read/Write</summary>

**Description**: The READ and WRITE host functions accept `path` arguments but `host_function_dispatcher.py` shows no path sanitization or sandboxing. An agent can invoke `READ` with `/proc/self/environ`, `/root/.ssh/id_rsa`, or database files to exfiltrate secrets, or `WRITE` to executable paths like `/agents/core/host_function_dispatcher.py` to achieve persistent code execution.

**Recommendation**: Implement strict path allowlisting using `acfs.manifest.yaml` declared directories only. Resolve all paths using `os.path.realpath()` and validate against an allowed directory list (chroot jail) before any filesystem operation. Reject paths containing `..` or absolute paths outside allowed roots.

</details>

<details>
<summary>🟡 <b>[MEDIUM]</b> Unbounded Async Task Creation in Message Bus</summary>

**Description**: `agents/gateway/bus.py` implements `publish_async` using `asyncio.create_task()` without semaphores, rate limits, or cancellation timeouts. An attacker can flood the bus with messages causing uncontrolled memory consumption and eventual OOM kills or service degradation (async task accumulation).

**Recommendation**: Implement `asyncio.Semaphore` to limit concurrent publish_async tasks (e.g., max 100 concurrent), add timeout controls (30s), and integrate with the existing circuit breaker configuration (`circuit_breaker.timeout_ms`) to prevent resource exhaustion.

</details>

<details>
<summary>🟡 <b>[MEDIUM]</b> ACFS Module Loading Without Runtime Verification</summary>

**Description**: `acfs.manifest.yaml` declares `hello_world` module with SHA-256 hash, but no verification logic is visible in the loading code. If the module file is modified on disk after manifest loading but before import, the system executes untrusted code despite the manifest checksum.

**Recommendation**: Implement TOCTOU-safe hash verification: compute SHA-256 of module file immediately before `importlib` loading and compare against manifest. Abort with `DROP_AND_QUARANTINE` action if hashes mismatch.

</details>

---

### 🟪 Purple Hat — AI Safety & Compliance
**Focus**: OWASP LLM Top 10, ALIGN rule coverage, epistemic modifier abuse, PII leakage

**Findings**: 9 (🔴 CRITICAL: 3, 🟠 HIGH: 2, 🟢 LOW: 1, 🟡 MEDIUM: 3)

<details>
<summary>🔴 <b>[CRITICAL]</b> Epistemic Modifier Gas Manipulation ([BELIEVE] Inflation)</summary>

**Description**: HLF runtime permits epistemic modifiers like [BELIEVE] that inflate confidence scores. If gas metering uses confidence as a divisor (higher confidence = lower cost), agents can prepend [BELIEVE] to arbitrary assertions to artificially reduce gas costs below actual compute requirements, effectively minting gas credits and bypassing tier limits (hearth: 1000). This violates economic consensus and allows resource exhaustion.

**Recommendation**: Decouple gas calculation from epistemic confidence. Gas must reflect actual compute/IO cost (as defined in host_functions.json) regardless of agent confidence modifiers. Implement ALIGN rule R-009: 'Reject gas calculations modified by epistemic state'.

</details>

<details>
<summary>🔴 <b>[CRITICAL]</b> Agent Template Integrity Bypass (seed_aegis_templates)</summary>

**Description**: db.py introduces seed_aegis_templates() which inserts high-privilege agent definitions (Sentinel, Arbiter) with SPAWN capabilities and elevated gas limits (50-100) directly into SQLite without cryptographic verification. If an attacker poisons the DB before seeding or modifies the template strings in transit, they can inject malicious system_prompts or restrictions that persist as 'trusted' Aegis agents.

**Recommendation**: Pre-compute SHA256 hashes of canonical Aegis templates and verify against governance/aegis_templates.sha256 before seeding. Templates must be signed by the Sovereign key and validated in the INSERT transaction.

</details>

<details>
<summary>🔴 <b>[CRITICAL]</b> Nested [DOUBT] Block Gas Evasion</summary>

**Description**: HLF runtime appears to support nested epistemic scopes via [DOUBT] blocks. If each nested scope instantiates isolated gas sub-accounts or triggers gas refunds on scope exit, agents can wrap expensive operations (OPENCLAW_SUMMARIZE: 7 gas) in nested [DOUBT] blocks to exploit refund mechanics, effectively achieving negative gas cost and evading the global gas_bucket.hearth limit of 1000.

**Recommendation**: Implement non-refundable, monotonic gas accounting that aggregates across all nested scopes. Disable gas refunds for [DOUBT] block exits; gas spent in child scopes must debit the parent scope irreversibly.

</details>

<details>
<summary>🟠 <b>[HIGH]</b> HLF Variable Prompt Injection (OWASP LLM01)</summary>

**Description**: Host functions like OPENCLAW_SUMMARIZE accept 'path' arguments (type: path) which are likely interpolated into shell commands or LLM prompts without sanitization visible in the diff. An attacker could inject malicious instructions via a crafted path string (e.g., '/tmp/file; rm -rf /') that bypasses R-001 regex blocks because the regex may not match within HLF variable interpolation contexts.

**Recommendation**: Implement strict parameterized HLF calls: validate path arguments against ALLOWLISTED_PATHS regex, use shlex.quote() for shell interactions, and never interpolate HLF variables into system prompts without HTML/JSON encoding.

</details>

<details>
<summary>🟠 <b>[HIGH]</b> Missing ALIGN Coverage for Agent Cognitive State</summary>

**Description**: Current ALIGN rules (R-001–R-008) cover shell commands and file access but lack rules for HLF epistemic constructs. Agents could encode malicious intents within [BELIEVE] or [DOUBT] blocks that evade regex-based intent scanning, as the semantic content is wrapped in epistemic qualifiers not inspected by current rules.

**Recommendation**: Add ALIGN rule R-010: 'Epistemic Sanitization' — recursively unpack [BELIEVE]/[DOUBT] blocks before applying R-001–R-008 regex scans. Reject agents with >3 nested epistemic modifiers as potential obfuscation attempts.

</details>

<details>
<summary>🟡 <b>[MEDIUM]</b> PII Leakage via Dream State Context (OWASP LLM06)</summary>

**Description**: enable_dreaming_state is active with analysis_model kimi-k2.5:cloud. Agent templates (Sentinel, Scribe) declare tools including READ and WEB_SEARCH (sensitive: true). Without explicit PII scrubbing in rolling_context or fact_store persistence, sensitive data processed during day cycles may persist unencrypted into dream state analysis, potentially leaking across tier boundaries (hearth → cloud).

**Recommendation**: Implement PII redaction (ner_regex for emails/SSNs) in the context serialization layer before dream state persistence. Encrypt tier-sensitive facts with tier-specific keys (hearth_key vs sovereign_key).

</details>

<details>
<summary>🟡 <b>[MEDIUM]</b> OpenClaw Binary Supply Chain Risk</summary>

**Description**: Host function OPENCLAW_SUMMARIZE declares binary_sha256 as empty string while enabling execution via docker_orchestrator. This permits undetected substitution of the OpenClaw binary with malicious code. Combined with 'sensitive: true' data handling, this creates a high-impact supply chain vulnerability (OWASP LLM07).

**Recommendation**: Populate binary_sha256 with the pinned hash of the approved OpenClaw binary. Implement binary integrity verification in the SPAWN pre-flight check; reject execution if SHA256 mismatch.

</details>

<details>
<summary>🟡 <b>[MEDIUM]</b> Tool Forge Sandbox Escape Potential</summary>

**Description**: New /data/tool_forge directory (711c9a755897e8187567e5958d89ee70cd40726931fcff638923723bb455eec7) is added with 700 permissions. If agents with SPAWN capability write executable code here and the Docker orchestrator mounts this directory without nosuid/noexec or seccomp-bpf profiles, container escape to host filesystem is possible.

**Recommendation**: Mount /data/tool_forge as read-only (ro) in SPAWN containers. Enable enable_seccomp: true in deployment settings. Enforce AppArmor profile 'docker-default' on all Tool Forge containers.

</details>

<details>
<summary>🟢 <b>[LOW]</b> Ollama Endpoint Insecure Communication</summary>

**Description**: ollama_dual configuration exposes localhost endpoints without mTLS (enable_mtls: false). While localhost binding reduces remote attack surface, lateral movement by compromised agents within the hearth tier could exploit these endpoints to exfiltrate model weights or poison responses. Additionally, ollama_allowed_models restrictions are not enforced in visible dispatcher code.

**Recommendation**: Enable enable_mtls: true for Ollama communication. Implement model allowlist enforcement in agents/core/tool_forge.py: reject dispatch requests to models not listed in ollama_allowed_models for the current tier.

</details>

---

### 🏆 Summary

| Hat | Verdict |
|-----|---------|
| ⚫ Black | 🔴 Critical Issues |
| 🟪 Purple | 🔴 Critical Issues |

**Total Findings**: 17 | **Critical**: 7

> 🔴 **BLOCK MERGE** — Critical issues must be resolved before merging.

---
*Generated by `hat_pr_review.py` at 2026-03-02 17:31:21 using Sovereign OS Hat Engine*