# De Bono Six Thinking Hats — Jules PR Review Protocol

> **Usage:** Every Jules PR must pass through all 6 hat perspectives before merge.
> This ensures changes are evaluated from every angle — not just functional correctness.
> Maps directly to the `hat_engine` used in the Dream Cycle (`dream_state.py` Stage 4).

---

## How Jules Uses the Hats

Each Jules PR is evaluated through 6 sequential perspectives. Jules must document findings under each hat before the PR is considered ready for human review.

### 🟢 GREEN Hat — Creativity & Expansion
**Question:** _"What new capabilities does this PR add? Does it expand the system's autonomous abilities?"_
- Are there creative ways this change could be extended further?
- Does it open doors for future autonomous improvements?
- Could the approach be applied to other parts of the system?
- **Required output:** At least 1 "next step" idea documented in PR description

### ⚪ WHITE Hat — Facts & Data
**Question:** _"What exactly changed? What are the objective metrics?"_
- Lines added vs removed (must be net-positive or zero for additive changes)
- Test count before vs after (must be >= baseline)
- Files touched — list every modified file with change summary
- Performance metrics: any benchmarks run?
- **Required output:** Change statistics table in PR description

### 🔴 RED Hat — Intuition & Feeling
**Question:** _"Does this feel right? Does it match the system's character?"_
- Does the code style match the existing codebase?
- Does the naming follow existing conventions?
- Does the change feel like it belongs in the Sovereign OS?
- Would a human reviewer find this change surprising or confusing?
- **Required output:** Gut-check verdict (Aligned / Uncertain / Misaligned)

### ⬛ BLACK Hat — Caution & Risk (CRITICAL)
**Question:** _"What could go wrong? What's the worst case?"_
- Could this break existing functionality? (Run full test suite)
- Does it introduce new external dependencies? (4GB RAM check)
- Could it create a security vulnerability? (ALIGN enforcement check)
- Is there any test deletion, simplification, or scope reduction?
- Does it violate Layer 1 constraints?
- **Required output:** Risk assessment with severity ratings

### 🟡 YELLOW Hat — Benefits & Value
**Question:** _"What's the upside? Why is this change valuable?"_
- Does it improve reliability, performance, or maintainability?
- Does it expand test coverage or governance?
- Does it make the system more autonomous?
- Does it improve the user experience (GUI, API, CLI)?
- **Required output:** Value proposition summary

### 🔵 BLUE Hat — Process & Meta (FINAL)
**Question:** _"Was the process correct? Is this PR ready?"_
- Did all 5 other hats complete their analysis?
- Were any RED flags raised that weren't addressed?
- Were any BLACK hat risks mitigated?
- Is the commit message comprehensive and accurate?
- Are all tests passing?
- **Required output:** Final Go/No-Go decision with justification

---

## Integration with Jules Workflow

```
Jules PR Created
    │
    ├─→ 🟢 GREEN: Creativity scan → document expansion opportunities
    ├─→ ⚪ WHITE: Factual audit → change statistics
    ├─→ 🔴 RED: Intuition check → style/convention alignment
    ├─→ ⬛ BLACK: Risk analysis → run tests, check invariants
    ├─→ 🟡 YELLOW: Value assessment → benefits summary
    └─→ 🔵 BLUE: Process review → Go/No-Go
         │
         ├─→ GO → Tag as "Hat-Reviewed" → Human review
         └─→ NO-GO → Auto-request changes → Jules fixes → Re-run hats
```

## Hat Review Template for PR Comments

```markdown
## 🎩 Six Hats Review

### 🟢 GREEN (Creativity)
- Expansion opportunities: [list]
- Next step ideas: [list]

### ⚪ WHITE (Facts)
| Metric | Before | After |
|--------|--------|-------|
| Test count | X | Y |
| Lines (net) | +N / -M |
| Files touched | N |

### 🔴 RED (Intuition)
- Style alignment: [Aligned/Uncertain/Misaligned]
- Notes: [any concerns]

### ⬛ BLACK (Risk)
- Breaking changes: [None / List]
- Security impact: [None / List]
- Invariant violations: [None / List]

### 🟡 YELLOW (Value)
- Key benefits: [list]

### 🔵 BLUE (Process)
- All hats complete: [Yes/No]
- Tests passing: [X/Y]
- **VERDICT: [GO / NO-GO]**
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
