# De Bono Eleven Thinking Hats — PR Review Protocol

> **Usage:** Every PR must pass through all 11 hat perspectives before merge.
> This ensures changes are evaluated from every angle — not just functional correctness.
> Maps directly to the `hat_engine` used in the Dream Cycle (`dream_state.py` Stage 4).

---

## How the Hats Work

Each PR is evaluated through 11 sequential perspectives. Findings are documented under each hat before the PR is considered ready for human review.

### Core Hats (De Bono)

### 🟢 GREEN Hat — Creativity & Expansion
**Question:** _"What new capabilities does this PR add? Does it expand the system's autonomous abilities?"_
- Are there creative ways this change could be extended further?
- Does it open doors for future autonomous improvements?
- **Required output:** At least 1 "next step" idea documented in PR description

### ⚪ WHITE Hat — Facts & Data
**Question:** _"What exactly changed? What are the objective metrics?"_
- Lines added vs removed
- Test count before vs after (must be >= baseline)
- Files touched — list every modified file with change summary
- **Required output:** Change statistics table in PR description

### 🔴 RED Hat — Intuition & Feeling
**Question:** _"Does this feel right? Does it match the system's character?"_
- Does the code style match the existing codebase?
- Does the naming follow existing conventions?
- **Required output:** Gut-check verdict (Aligned / Uncertain / Misaligned)

### ⬛ BLACK Hat — Caution & Risk (CRITICAL)
**Question:** _"What could go wrong? What's the worst case?"_
- Could this break existing functionality? (Run full test suite)
- Does it introduce new external dependencies? (4GB RAM check)
- Could it create a security vulnerability? (ALIGN enforcement check)
- Does it violate Layer 1 constraints?
- **Required output:** Risk assessment with severity ratings

### 🟡 YELLOW Hat — Benefits & Value
**Question:** _"What's the upside? Why is this change valuable?"_
- Does it improve reliability, performance, or maintainability?
- Does it expand test coverage or governance?
- **Required output:** Value proposition summary

### 🔵 BLUE Hat — Process & Meta (CHECKPOINT)
**Question:** _"Was the process correct so far?"_
- Did all 5 core hats complete their analysis?
- Were any RED flags raised that weren't addressed?
- Were any BLACK hat risks mitigated?
- **Required output:** Interim Go/Hold decision

### Extended Hats (Sovereign OS)

### 🟣 INDIGO Hat — Cross-Feature Architecture
**Question:** _"Does this create redundancy? Could it consolidate existing components?"_
- Does the change duplicate logic that exists elsewhere in the system?
- Could MoMA routing and gateway functions be consolidated?
- Are there DRY violations at the macro-architecture level?
- **Required output:** Consolidation opportunities (or "None — no overlap detected")

### 🩵 CYAN Hat — Innovation & Feasibility
**Question:** _"Does this open paths to HLF extensions or new features?"_
- Could this change enable new grammar rules or statement types?
- Are there A2A integration opportunities?
- Is any proposed feature grounded in production-ready tech?
- **Required output:** Forward-looking opportunities with feasibility rating

### 🟪 PURPLE Hat — AI Safety & Compliance
**Question:** _"Does this create new attack surfaces or compliance gaps?"_
- OWASP LLM Top 10 check against any new endpoints
- Does it weaken ALIGN rule enforcement?
- Could epistemic modifiers be abused through this change?
- Does it leak PII through rolling context or fact store?
- **Required output:** Safety assessment with OWASP references if applicable

### 🟠 ORANGE Hat — DevOps & Automation
**Question:** _"Is the CI/CD pipeline still correct? Does this deploy cleanly?"_
- Does the GitHub Actions workflow cover this change?
- Are Docker configs updated if needed?
- Are dependencies properly pinned?
- **Required output:** Deployment readiness verdict

### 🪨 SILVER Hat — Context & Token Optimization
**Question:** _"Does this change affect token costs or context efficiency?"_
- Does it increase system prompt sizes?
- Does it affect gas budget calculations?
- Are there prompt compression opportunities?
- **Required output:** Token impact assessment (Neutral / Increase / Decrease)

---

## Integration with Workflow

```text
PR Created
    │
    ├─→ 🟢 GREEN: Creativity scan → expansion opportunities
    ├─→ ⚪ WHITE: Factual audit → change statistics
    ├─→ 🔴 RED: Intuition check → style alignment
    ├─→ ⬛ BLACK: Risk analysis → run tests, check invariants
    ├─→ 🟡 YELLOW: Value assessment → benefits summary
    ├─→ 🔵 BLUE: Process checkpoint → interim Go/Hold
    │     │
    │     └─→ If HOLD → fix issues → re-run core hats
    │
    ├─→ 🟣 INDIGO: Architecture review → consolidation check
    ├─→ 🩵 CYAN: Innovation scan → feasibility check
    ├─→ 🟪 PURPLE: Safety audit → OWASP/ALIGN check
    ├─→ 🟠 ORANGE: DevOps audit → CI/CD/Docker check
    └─→ 🪨 SILVER: Token review → efficiency assessment
         │
         ├─→ ALL CLEAR → Tag as "Hat-Reviewed" → Human review
         └─→ ISSUES → Auto-request changes → Fix → Re-run
```

## Hat Review Template for PR Comments

```markdown
## 🎩 Eleven Hats Review

### Core Hats
| Hat | Verdict | Notes |
|-----|---------|-------|
| 🟢 GREEN | [Expand/Neutral] | [notes] |
| ⚪ WHITE | +N/-M lines, X tests | [stats] |
| 🔴 RED | [Aligned/Misaligned] | [notes] |
| ⬛ BLACK | [Risk level] | [notes] |
| 🟡 YELLOW | [Value summary] | [notes] |
| 🔵 BLUE | [Go/Hold] | [notes] |

### Extended Hats
| Hat | Verdict | Notes |
|-----|---------|-------|
| 🟣 INDIGO | [No overlap/Consolidate] | [notes] |
| 🩵 CYAN | [Opportunities/None] | [notes] |
| 🟪 PURPLE | [Safe/Risk] | [notes] |
| 🟠 ORANGE | [Ready/Needs work] | [notes] |
| 🪨 SILVER | [Neutral/Impact] | [notes] |

**VERDICT: [GO / NO-GO]**
```

## Connection to Existing Systems

| Hat | Existing System Component | How It Maps |
|-----|--------------------------|-------------|
| 🟢 GREEN | `dream_state.py` Stage 3 (HLF Practice) | Creative generation of new patterns |
| ⚪ WHITE | `ALSLogger` (Merkle chain) | Factual audit trail |
| 🔴 RED | `hat_engine.py` Red Hat analysis | Emotional/intuitive assessment |
| ⬛ BLACK | `sentinel_gate.py` (ALIGN enforcement) | Risk detection & blocking |
| 🟡 YELLOW | `scoring.py` (tier assessment) | Value quantification |
| 🔵 BLUE | `local_autonomous.py` (orchestration) | Process coordination |
| 🟣 INDIGO | Pipeline gate consolidation | Cross-feature DRY analysis |
| 🩵 CYAN | HLF grammar extensions / A2A | Innovation scouting |
| 🟪 PURPLE | ALIGN rules + OWASP LLM Top 10 | Safety & compliance enforcement |
| 🟠 ORANGE | GitHub Actions / Docker / CI/CD | Operational infrastructure |
| 🪨 SILVER | Gas metering / rolling context | Token efficiency |
