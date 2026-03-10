# Security Architecture

> **Sovereign Agentic OS — Zero-Trust Security Deep Dive**
> Updated: 2026-03-10 | Applies to all deployment tiers (hearth / forge / sovereign)

Every agent intent passes through **6 deterministic security gates** before any code executes. This document details each gate, the supporting components, and the immutable governance rules that make the Sovereign OS resistant to the attack classes described in `README.md`.

---

## Table of Contents

1. [The 6-Gate Pipeline](#1-the-6-gate-pipeline)
2. [ALIGN Ledger (Immutable Governance Rules)](#2-align-ledger-immutable-governance-rules)
3. [ACFS Confinement & Worktree Isolation](#3-acfs-confinement--worktree-isolation)
4. [Gas Budget & Rate Limiting](#4-gas-budget--rate-limiting)
5. [Nonce Replay Protection](#5-nonce-replay-protection)
6. [Merkle-Chain Audit Trail (ALS)](#6-merkle-chain-audit-trail-als)
7. [Seccomp Profile](#7-seccomp-profile)
8. [AST Injection Validator](#8-ast-injection-validator)
9. [Dead Man's Switch](#9-dead-mans-switch)
10. [KYA — Know Your Agent (Identity)](#10-kya--know-your-agent-identity)
11. [Threat Model Summary](#11-threat-model-summary)

---

## 1. The 6-Gate Pipeline

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Gate 1: validate_hlf()         → Regex structural pre-check            │
│  Gate 2: hlfc.compile()         → LALR(1) parse + arity/type checking   │
│  Gate 3: hlflint.lint()         → Token budget + gas + unused vars      │
│  Gate 4: enforce_align()        → Immutable regex block (ALIGN Ledger)  │
│  Gate 5: verify_gas_limit()     → Per-intent + per-tier gas bucket      │
│  Gate 6: Nonce check (Redis)    → ULID replay protection (TTL=600s)     │
└─────────────────────────────────────────────────────────────────────────┘
       ↓ passes all 6                     ↓ fails any gate
  Execute via Dapr mTLS         HTTP 403 / 409 / 422 / 429 + ALS log
```

**Key files:**
- `agents/gateway/bus.py` — orchestrates Gates 1–6 in the middleware chain
- `agents/gateway/sentinel_gate.py` — implements Gate 4 (ALIGN enforcement)
- `agents/gateway/router.py` — implements Gate 5 (gas metering)
- `hlf/__init__.py` — Gate 1 (`validate_hlf`)
- `hlf/hlfc.py` — Gate 2 (compiler)
- `hlf/hlflint.py` — Gate 3 (linter)

---

## 2. ALIGN Ledger (Immutable Governance Rules)

File: `governance/ALIGN_LEDGER.yaml`

The ALIGN Ledger contains **9 immutable security rules** compiled at startup by `sentinel_gate.py`. Rules are evaluated as regex patterns and conditions against every intent payload. A match triggers the configured action: `DROP`, `DROP_AND_QUARANTINE`, or `ROUTE_TO_HUMAN_APPROVAL`.

| Rule | Name | What it blocks | Action |
|------|------|----------------|--------|
| R-001 | ACFS Confinement | Shell escape sequences (`/bin/sh`, `rm -rf /`, `curl | bash`) | DROP_AND_QUARANTINE |
| R-002 | Self-Modification Freeze | Intents targeting `sentinel_gate.py` | ROUTE_TO_HUMAN_APPROVAL |
| R-003 | Docker Socket Block | Docker socket paths | DROP_AND_QUARANTINE |
| R-004 | Env Exfiltration Block | `.env`, `VAULT_SECRET`, `API_KEY` references | DROP_AND_QUARANTINE |
| R-005 | Outbound Network Block | `curl`, `wget`, `nc`, `ncat` | DROP |
| R-006 | Privilege Escalation Block | `sudo`, `su -`, `chmod 777` | DROP_AND_QUARANTINE |
| R-007 | Process Injection Block | `os.system`, `subprocess.call`, `eval(`, `exec(`, `__import__` | DROP_AND_QUARANTINE |
| R-008 | OpenClaw Key Block | Raw `openclaw:` references | DROP |
| R-009 | AST Aliasing Bypass Block | `import X as Y` aliasing patterns | DROP_AND_QUARANTINE |

**Design invariants:**
- Rules are loaded *read-only* (`governance/` is mounted `:ro` in all Docker containers)
- Rules are compiled to regex at startup — zero runtime modification possible
- `LLM_Judge` in `sentinel_gate.py` additionally evaluates proposed file diffs against ALIGN constraints before any commit

---

## 3. ACFS Confinement & Worktree Isolation

**ACFS (Agent-Controlled Filesystem)** enforces directory-level permission boundaries for all agents.

| Directory | Permissions | Purpose |
|-----------|-------------|---------|
| `/data` | `700` | Runtime data — SQLite, cold archive, quarantine |
| `/governance` | `555` | Read-only governance rules |
| `/agents` | `555` | Read-only agent code |
| `/security` | `555` | Read-only seccomp profile |

**ACFS Worktree Isolation** (`agents/core/acfs.py`):
- Each agent mission executes inside a physically isolated Git worktree via `ACFSWorktreeManager`
- Shadow commits include ALIGN-Merkle hashes for forensic tracing
- Stale worktrees are auto-cleaned after configurable thresholds
- Parallel agents cannot overwrite each other's work-in-progress

---

## 4. Gas Budget & Rate Limiting

**Gas** is measured in AST node count (not raw tokens). This prevents agents from crafting artificially complex programs to exhaust resources.

### Per-intent limit
Set by `MAX_GAS_LIMIT` env var (default: 10 AST nodes).

### Per-tier global bucket
Replenished nightly via cron:

| Tier | Daily Gas Budget |
|------|-----------------|
| hearth | 1,000 |
| forge | 10,000 |
| sovereign | 100,000 |

Gas is decremented atomically via a Lua script in Redis (prevents race conditions). Exceeding either limit returns HTTP 429.

### Rate limiting
50 RPM token bucket per source IP, implemented in `agents/gateway/bus.py` via Redis `INCR + EXPIRE`.

---

## 5. Nonce Replay Protection

Every intent is stamped with a **ULID** (Universally Unique Lexicographically Sortable Identifier) at ingestion. The ULID is stored in Redis with a 600-second TTL using `SETNX` (set-if-not-exists). A duplicate ULID within the TTL window returns HTTP 409 Conflict.

This prevents replay attacks where a valid intent is captured and re-submitted.

---

## 6. Merkle-Chain Audit Trail (ALS)

**ALS (Agentic Log Standard)** produces a cryptographic audit trail for every event.

### Log schema (JSON)
```json
{
  "trace_id": "<SHA-256 of parent_hash + payload>",
  "parent_trace_hash": "<previous trace_id>",
  "timestamp": "<ISO-8601>",
  "goal_id": "<ULID>",
  "agent_role": "<role name>",
  "confidence_score": 0.0,
  "anomaly_score": 0.0,
  "token_cost": 0
}
```

Each `trace_id` is computed as `SHA-256(parent_hash + payload)` — making the chain non-repudiable. The genesis hash is `0000...0000` (64 zeros), stored in `observability/openllmetry/last_hash.txt`.

### Semantic Outlier Trap
If `anomaly_score > 0.85`, the logger automatically fires a webhook to `sentinel_gate.py` for quarantine evaluation.

**Key file:** `agents/core/logger.py`

---

## 7. Seccomp Profile

File: `security/seccomp.json`

All containers run under a custom seccomp profile with `defaultAction: SCMP_ACT_ERRNO`. Only an explicit allow-list of syscalls is permitted.

**Explicitly denied:**
`ptrace`, `kexec_load`, `mount`, `umount2`, `pivot_root`, `reboot`, `sethostname`, `init_module`, `delete_module`, `acct`

This prevents kernel-level privilege escalation even if an agent somehow escapes the application-level controls.

---

## 8. AST Injection Validator

File: `agents/core/ast_validator.py`

Before any generated Python code is executed, `ast_validator.py` uses the Python `ast` module to statically walk the syntax tree and reject code containing:
- `os.system` calls
- `subprocess.*` calls
- `eval()` / `exec()` calls

Returns a `(bool, list[str])` tuple — `True` if safe, plus a list of any detected violations.

---

## 9. Dead Man's Switch

File: `agents/core/dead_man_switch.py`

A background watchdog severs the gateway's outbound network path if **3 panics occur within 5 minutes**. This prevents runaway agents from exfiltrating data or escalating API spend after a compromise.

Panics are recorded to the ALS stream and trigger an automatic `DROP_AND_QUARANTINE` state on the gateway.

---

## 10. KYA — Know Your Agent (Identity)

Agents authenticate using **SPIFFE/X.509 certificates** generated by `governance/kya_init.sh`. Each certificate is scoped to a specific agent role and tier.

**Certificate fields:**
- `CN` — Agent role (e.g., `sentinel`, `scribe`, `arbiter`)
- `SAN` — Deployment tier and service ULID
- `O` — `SovereignOS`

Dapr mTLS (enabled on forge / sovereign tiers) uses these certificates for service-to-service authentication. No inter-service call is possible without a valid KYA certificate.

---

## 11. Threat Model Summary

| Threat Class | Mitigation Layer | Gate |
|-------------|-----------------|------|
| Prompt Injection | ALIGN Ledger R-001–R-009 regex blocks | Gate 4 |
| Infinite Loop / DoS | Gas budget + Redis token bucket | Gate 5 |
| Privilege Escalation | Seccomp + ACFS + R-006 | Gate 4 + OS |
| Supply Chain Poisoning | SHA-256 content pinning + SLSA-3 | CI/CD |
| Replay Attack | ULID nonce + 600s TTL Redis | Gate 6 |
| Silent Data Exfiltration | Air-gapped egress proxy + R-005 | Gate 4 + Network |
| Memory Poisoning | Vector race protection + Merkle audit | Memory layer |
| Runaway Spending | Per-intent + per-tier gas buckets | Gate 5 |
| Code Injection | AST validator + R-007 | Gate 4 + Runtime |
| Agent Impersonation | KYA SPIFFE/x509 + Dapr mTLS | Identity layer |

---

*For the full ALIGN Ledger source, see `governance/ALIGN_LEDGER.yaml`.*
*For seccomp policy source, see `security/seccomp.json`.*
*For gas metering implementation, see `agents/gateway/router.py`.*
