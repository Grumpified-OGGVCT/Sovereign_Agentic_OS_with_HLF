# Jules ↔ Antigravity Coordination Protocol

## Overview

This document defines the coordination protocol between **Jules** (Google's coding agent, running in the cloud sandbox) and **Antigravity** (the local Gemini agent), working on the same repo: `Grumpified-OGGVCT/Sovereign_Agentic_OS_with_HLF`.

## Architecture

```
┌─────────────┐     GitHub      ┌─────────────────┐
│   Jules      │ ←──── PR ────→ │   Antigravity    │
│  (Cloud)     │    (main)      │   (Local Dev)    │
│              │                │                  │
│  Branch:     │                │  Branch:         │
│  jules/*     │                │  instinct/*      │
└─────────────┘                 └──────────────────┘
```

## Branch Contract

| Agent | Branch Pattern | Scope |
|-------|---------------|-------|
| **Jules** | `jules/*` | Issue-driven work, refactors, test gaps, docs |
| **Antigravity** | `instinct/*` | Core build, new modules, architecture, integration |
| **Shared** | `main` | Merge target — both agents PR into main |

## Sync Protocol

### Pull-before-push
Both agents MUST `git pull origin main` (or rebase) before pushing to avoid conflicts.

### PR-based coordination
- No direct pushes to `main`
- All work goes through PRs for auditability
- Copilot review enabled on all PRs

### Conflict resolution priority
1. **Antigravity** owns: `agents/core/*`, `governance/*`, `hlf/*` (new modules)
2. **Jules** owns: `hlf/runtime.py`, `hlf/hlfc.py`, `tools/*` (runtime refinement)
3. **Shared**: `tests/*`, `README.md`, `TODO.md` — last merge wins with rebase

## Cron Schedule (4x Daily)

| Cycle | Time (CT) | Jules Focus | Expected Output |
|-------|-----------|-------------|-----------------|
| **Dawn** | 06:00 | Pick top issue, plan + code | Draft PR |
| **Noon** | 12:00 | Test fixes, refactors, docs | Follow-up commits |
| **Dusk** | 18:00 | Self-testing HLF programs | New HLF examples + bug reports |
| **Night** | 00:00 | Codebase health: lint, types, dead code | Cleanup PR |

### Scaling Plan (Future Goal)
- **Phase 1**: 4 cron jobs/day (current)
- **Phase 2**: 8 cron jobs/day (every 3 hours) — when cycle time < 2h average
- **Phase 3**: 12 cron jobs/day (every 2 hours) — maximize daily sprints
- **Ultimate**: Continuous — Jules runs indefinitely, Antigravity merges/reviews

## Handoff Document: SESSION_HANDOVER.md

Both agents read/write `SESSION_HANDOVER.md` at repo root:

```markdown
# Session Handover
## Last Agent: [Jules|Antigravity]
## Timestamp: [ISO-8601]
## Branch: [branch-name]
## Status: [building|testing|blocked|ready-for-review]
## Changes This Cycle:
- [list of files changed]
## Tests: [pass count] / [total]
## Next Priority:
- [what the other agent should pick up]
## Blockers:
- [any issues needing human input]
```

## Jules Task Template (GitHub Issues)

When creating issues for Jules to pick up:

```markdown
**Title**: [Component]: [Brief description]

**Context**: [Why this is needed, what depends on it]

**Acceptance Criteria**:
- [ ] Implementation complete
- [ ] Tests pass (pytest -q)
- [ ] No ruff lint errors
- [ ] SESSION_HANDOVER.md updated

**Branch**: jules/[feature-name]
**Priority**: [P0|P1|P2]
**Labels**: jules-task, [component-label]
```

## Current State (2026-03-09)

- **Active PR**: #83 (Wave 5+6, 1,733 tests)
- **Jules Session**: `11017930320300550453` — working on HLF runtime self-coding
- **Antigravity**: Core build complete — ASB, MAESTRO Router, ADR system
- **Next for Jules**: Wire `dapr_file_write`/`dapr_file_read` backends in `hlf/runtime.py`
