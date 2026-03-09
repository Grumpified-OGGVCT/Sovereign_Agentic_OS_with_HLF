# Language Reference

> **Source of Truth**: [`governance/hls.yaml`](https://github.com/Grumpified-OGGVCT/Sovereign_Agentic_OS_with_HLF/blob/main/governance/hls.yaml) (BNF grammar) and [`governance/templates/dictionary.json`](https://github.com/Grumpified-OGGVCT/Sovereign_Agentic_OS_with_HLF/blob/main/governance/templates/dictionary.json) (tag definitions)

---

## Program Structure

Every HLF program consists of one or more statements followed by the `Ω` terminator:

```hlf
[HLF-v2]
[SET] x = 42
[INTENT] compute "result"
[RESULT] 0 "done"
Ω
```

---

## Tags

Tags are the primary statement type. They use bracket syntax: `[TAG] arg1 arg2 ...`

### Core Tags

| Tag | Arity | Arguments | Notes |
|-----|-------|-----------|-------|
| `INTENT` | 2 | `action:string target:path` | Primary agent instruction |
| `THOUGHT` | 1 | `reasoning:string` | Pure (no side effects) |
| `OBSERVATION` | 1 | `data:any` | Pure |
| `PLAN` | 1+ | `steps:any (repeat)` | Multi-step plan |
| `CONSTRAINT` | 2 | `key:string value:any` | Runtime constraint |
| `EXPECT` | 1 | `outcome:string` | Expected result |
| `ACTION` | 1+ | `verb:string args:any (repeat)` | Concrete action |
| `SET` | 2 | `name:identifier value:any` | Immutable binding |
| `FUNCTION` | 1+ | `name:identifier args:any (repeat)` | Pure function |
| `DELEGATE` | 2 | `role:identifier intent:string` | Agent delegation |
| `VOTE` | 2 | `decision:bool rationale:string` | Multi-agent vote |
| `ASSERT` | 2 | `condition:bool error:string` | Runtime assertion |
| `RESULT` | 2 | `code:int message:string` | Terminator |
| `MODULE` | 1 | `name:identifier` | Namespace declaration |
| `IMPORT` | 1 | `name:identifier` | Module import |
| `DATA` | 1 | `id:string` | Data reference |

### Extended Tags

| Tag | Arity | Arguments | Notes |
|-----|-------|-----------|-------|
| `MEMORY` | 3 | `entity:string content:any confidence:any` | Memory store |
| `RECALL` | 2 | `entity:string top_k:int` | Memory retrieval |
| `DEFINE` | 2 | `name:string body:any` | Macro definition |
| `CALL` | 1+ | `name:string args:any (repeat)` | Function call |
| `WHILE` | 1+ | `condition:string body:any (repeat)` | Loop |
| `TRY` | 1+ | `body:any (repeat)` | Error handling |
| `CATCH` | 1+ | `handler:any (repeat)` | Error handler |
| `RETURN` | 1 | `value:any` | Return value |

---

## Glyphs

Unicode glyphs modify execution semantics:

| Glyph | Name | Role |
|-------|------|------|
| `Ω` | Terminal Conclusion | Program terminator |
| `Δ` | State Diff | Output only delta changes |
| `Ж` | Reasoning Blocker | Triggers Arbiter on paradox |
| `⩕` | Gas Metric | Recursion/step limit |
| `⌘` | Orchestrator Directive | Macro override command |
| `∇` | Anchor Drift | Provenance bounds alarm |
| `⨝` | Matrix Consensus | Multi-agent sync barrier |
| `⌂` | Memory Anchor | HLF-anchored memory store |

---

## RFC 9005 Extensions

### Tool Execution

```hlf
↦ τ(io.fs.read) :: 𝕊 "/etc/config"
```

- `↦` — Tool prefix
- `τ(name)` — Tool marker with dotted name
- `:: TYPE` — Optional type annotation

### Conditional Logic

```hlf
⊎ x > 10 ⇒ [RESULT] 0 "ok" ⇌ [RESULT] 1 "too small"
```

- `⊎` — IF
- `⇒` — THEN
- `⇌` — ELSE (optional)

Boolean operators: `¬` (NOT), `∩` (AND), `∪` (OR)

### Assignment

```hlf
result :: 𝕊 ← ↦ τ(io.fs.read) /etc/config
```

- `←` — Assignment arrow
- Tool output or expression on the right

!!! warning "Known Limitation"
    The `←` assign syntax in `hlf_programs/` is not yet supported by the LALR(1) grammar at parse time. Fixing the grammar would enable full end-to-end autonomous execution.

### Type Symbols

| Symbol | Type | Description |
|--------|------|-------------|
| `𝕊` | string | String type |
| `ℕ` | number | Number type |
| `𝔹` | boolean | Boolean type |
| `𝕁` | json | JSON object type |
| `𝔸` | any | Dynamic type |

### Concurrency

```hlf
∥ [↦ τ(io.fs.read) /a, ↦ τ(io.fs.read) /b]
⋈ [file_a, file_b] → [INTENT] merge_results
```

- `∥` — Parallel execution block
- `⋈ ... →` — Sync barrier then execute

---

## Compilation Pipeline

The HLF compiler (`hlfc`) runs 4 passes:

1. **Parse** — LALR(1) grammar → concrete syntax tree
2. **Collect Environment** — Gather immutable `[SET]` bindings
3. **Expand & Validate** — Resolve `${VAR}` references
4. **ALIGN Validate** — Enforce security rules from `ALIGN_LEDGER.yaml`
5. **Dictionary Validate** — Check tag arity/types against `dictionary.json`

---

## Semantic Constraints

- `SET` bindings are **immutable** — duplicate `SET` raises `HlfSyntaxError`
- All `${VAR}` references must resolve — unresolved raises `HlfSyntaxError`
- ALIGN Ledger rules block dangerous content — violations raise `HlfAlignViolation`
- Tag arguments must match `dictionary.json` arity — violations raise `HlfArityError`
- `RESULT` is a terminator — runtime stops after `[RESULT]`
- `FUNCTION` nodes are pure — no side effects permitted
