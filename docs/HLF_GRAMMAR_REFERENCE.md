# HLF Grammar Reference v0.4.0

> Authoritative operator catalog for all agents. Consult before composing HLF.
> RFC 9005 v3.0 + RFC 9007 | Updated: 2026-03-01

---

## Statement Types (14 total)

### Core Statements (v0.1 — backward-compatible)

| Syntax | Tag | Human-Readable |
|--------|-----|----------------|
| `[TAG] args...` | tag_stmt | Execute TAG operation with arguments |
| `[SET] name=value` | set_stmt | Bind immutable variable 'name' to value |
| `[FUNCTION] name args...` | function_stmt | Define function 'name' with arguments |
| `[RESULT] args...` | result_stmt | Return result with arguments |
| `[MODULE] name` | module_stmt | Declare module 'name' |
| `[IMPORT] name` | import_stmt | Import module 'name' |

### RFC 9005 Operators (v0.4.0)

| Syntax | Tag | Human-Readable |
|--------|-----|----------------|
| `↦ τ(tool.name) args...` | TOOL | Execute tool 'tool.name' |
| `⊎ condition ⇒ then_stmt` | CONDITIONAL | IF condition THEN execute |
| `⊎ condition ⇒ then ⇌ else` | CONDITIONAL | IF condition THEN execute ELSE alternate |
| `name ← value` | ASSIGN | Assign value to variable 'name' |
| `∥ [ task1, task2, ... ]` | PARALLEL | Execute tasks concurrently |
| `⋈ [ ref1, ref2 ] → stmt` | SYNC | Wait for refs then execute statement |

### RFC 9007 Operators (v0.4.0)

| Syntax | Tag | Human-Readable |
|--------|-----|----------------|
| `name ≡ { field:Type, ... }` | STRUCT | Define struct 'name' with typed fields |

### Glyph Prefixes (Statement Modifiers)

| Glyph | Meaning | Example |
|-------|---------|---------|
| `⌘` | EXECUTE | `⌘ [DEPLOY] stack="prod"` |
| `Ж` | CONSTRAINT | `Ж [CONSTRAINT] tier="forge"` |
| `∇` | PARAMETER | `∇ [PARAM] timeout=30` |
| `⩕` | PRIORITY | `⩕ [PRIORITY] level="urgent"` |
| `⨝` | JOIN | `⨝ [MERGE] sources="a,b"` |
| `Δ` | DELTA/CHANGE | `Δ [UPDATE] config="new"` |
| `~` | APPROXIMATE | `~ [ESTIMATE] cost=100` |
| `§` | SECTION | `§ [SECTION] name="auth"` |

---

## Conditional Logic Operators

| Operator | Meaning | Example |
|----------|---------|---------|
| `¬` | NOT (negation) | `⊎ ¬ error ⇒ [RESULT] ok=true` |
| `∩` | AND (intersection) | `⊎ ready ∩ valid ⇒ [DEPLOY]` |
| `∪` | OR (union) | `⊎ cached ∪ fresh ⇒ [SERVE]` |

## Math Expression Operators

| Operator | Meaning | Precedence |
|----------|---------|------------|
| `+` | Addition | Low |
| `-` | Subtraction | Low |
| `*` | Multiplication | High |
| `/` | Division | High |
| `()` | Grouping | Highest |

## Comparison Operators

| Operator | Meaning |
|----------|---------|
| `==` | Equal |
| `!=` | Not equal |
| `>=` | Greater or equal |
| `<=` | Less or equal |
| `>` | Greater than |
| `<` | Less than |

---

## Type Annotations

| Symbol | Type | Description |
|--------|------|-------------|
| `𝕊` | String | Text value |
| `ℕ` | Number | Numeric value (int or float) |
| `𝔹` | Boolean | true or false |
| `𝕁` | JSON | Structured data |
| `𝔸` | Any | No type constraint |

Usage: `name :: 𝕊 ← "hello"` — assign string "hello" to name with type annotation.

---

## Special Constructs

### Pass-by-Reference
`& varname` — Pass variable by reference (mutable in callee scope).

### Epistemic Modifier
`_{ρ:0.85}` — Attach confidence score ρ ∈ [0,1] to any expression.

### Terminator
Every HLF program MUST end with `Ω` (Greek capital omega) or `Omega`.

### Version Header
`[HLF-v3]` — Ignored by parser, used for version tracking.

---

## Error Correction Feedback Loop

When an agent sends malformed HLF, the compiler returns a structured correction via `format_correction()`:

```json
{
  "error": "No terminal matches 'X' in the current parser context",
  "source": "<the malformed HLF source>",
  "correction_hlf": null,
  "human_readable": "HLF compilation failed: ... Review the valid operator list.",
  "valid_operators": { "↦ τ(tool.name)": "Execute a tool (RFC 9005 §4.1)", ... },
  "suggestion": "Consult docs/HLF_GRAMMAR_REFERENCE.md before composing HLF."
}
```

Agents MUST:
1. Parse the `valid_operators` catalog
2. Identify the correct operator for their intent
3. Rewrite their HLF using the correct syntax
4. Retry compilation
5. If still failing, escalate to the Iterative Intervention Engine

---

## InsAIts V2 Transparency Mandate

Every AST node produced by `hlfc.compile()` includes a `human_readable` field:

```json
{
  "tag": "ASSIGN",
  "operator": "←",
  "target": "count",
  "type_annotation": {"type": "ℕ"},
  "value": 42,
  "human_readable": "Assign 42 to 'count' with type Number"
}
```

This field MUST be preserved in all downstream processing. It enables:
- Human audit of agent decisions
- Debugging without HLF expertise
- Compliance verification

---

## Quick Reference Card

```
[HLF-v3]                          ← Version header (ignored)
[SET] name="value"                 ← Immutable binding
[INTENT] action "target"           ← High-level intent
↦ τ(tool.api.call) arg="val"      ← Execute tool
⊎ condition ⇒ [RESULT] ok=true    ← Conditional
  ⇌ [RESULT] ok=false             ← Else branch
name :: 𝕊 ← "hello"               ← Typed assignment
∥ [ [TASK] a, [TASK] b ]           ← Parallel execution
⋈ [ a, b ] → [MERGE] result       ← Sync barrier
Config ≡ { host:𝕊, port:ℕ }       ← Struct definition
⌘ [DEPLOY] stack="prod"            ← Glyph-prefixed statement
Ω                                  ← Terminator (REQUIRED)
```
