# CLI Tools

The HLF toolchain provides 6 command-line tools for development, testing, and package management.

---

## hlfc — Compiler

Compiles `.hlf` source files to JSON AST with 4-pass validation.

```bash
python -m hlf.hlfc program.hlf
python -m hlf.hlfc program.hlf -o output.json
python -m hlf.hlfc program.hlf --tier forge
```

### Features

- LALR(1) grammar parsing via Lark
- Immutable `[SET]` environment collection
- `${VAR}` reference expansion
- ALIGN Ledger security validation
- `dictionary.json` arity/type checking
- Structured error reporting with line numbers

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Syntax error |
| 2 | ALIGN violation |
| 3 | Arity/type error |

---

## hlflint — Linter

Static analysis for HLF style and best practices.

```bash
python -m hlf.hlflint program.hlf
python -m hlf.hlflint program.hlf --strict
python -m hlf.hlflint hlf/stdlib/
```

### Rules

- Missing `Ω` terminator
- Unused `[SET]` bindings
- Duplicate tag sequences
- Style warnings (line length, naming conventions)
- ALIGN compliance checks

---

## hlffmt — Formatter

Auto-format HLF source code to canonical style.

```bash
python -m hlf.hlffmt program.hlf
python -m hlf.hlffmt program.hlf --in-place
```

### Formatting Rules

- One statement per line
- Consistent tag bracket spacing
- Sorted `[SET]` bindings
- Canonical glyph rendering

---

## hlfsh — REPL

Interactive HLF shell with persistent environment and gas metering.

```bash
python -m hlf.hlfsh
python -m hlf.hlfsh --tier forge
python -m hlf.hlfsh --gas-limit 500
```

### Commands

| Command | Description |
|---------|-------------|
| `:help` | Show available commands |
| `:env` | Display current environment |
| `:gas` | Show gas usage |
| `:reset` | Reset environment |
| `:load FILE` | Load and execute a `.hlf` file |
| `:tier TIER` | Switch deployment tier |
| `:modules` | List loaded modules |
| `:quit` | Exit REPL |

### Features

- Persistent environment across statements
- Gas metering per statement
- Module loading via `[IMPORT]`
- Tab completion for tags
- History with readline support

---

## hlfpm — Package Manager

Install, manage, and distribute HLF modules via OCI registries.

```bash
python -m hlf.hlfpm install my_module
python -m hlf.hlfpm install my_module@1.0.0
python -m hlf.hlfpm uninstall my_module
python -m hlf.hlfpm list
python -m hlf.hlfpm search "auth"
python -m hlf.hlfpm update my_module
python -m hlf.hlfpm freeze > requirements.hlf
```

### Commands

| Command | Description |
|---------|-------------|
| `install PKG[@VER]` | Install a module (local or OCI) |
| `uninstall PKG` | Remove a module |
| `list` | List installed modules |
| `search QUERY` | Search available modules |
| `update PKG` | Update to latest version |
| `freeze` | Output installed modules with versions |

### Configuration

OCI registry settings in `config/settings.json`:

```json
{
  "modules": {
    "oci_registry": "ghcr.io",
    "oci_namespace": "Grumpified-OGGVCT/hlf-modules",
    "oci_enabled": false,
    "cache_dir": "hlf/.oci_cache"
  }
}
```

---

## hlftest — Test Harness

Run compile + lint + gas validation on `.hlf` files with pytest integration.

```bash
python -m hlf.hlftest hlf/stdlib/
python -m hlf.hlftest hlf/modules/ --strict
python -m hlf.hlftest --gas-limit 50
```

### Features

- Batch test runner for `.hlf` files
- Compile verification (syntax errors = failure)
- Lint checking (warnings by default, errors with `--strict`)
- Gas budget validation
- pytest plugin: `--hlf-dir` flag for auto-discovery

### Assertion Helpers (for Python tests)

```python
from hlf.hlftest import assert_compiles, assert_lints_clean, assert_gas_under

assert_compiles("program.hlf")
assert_lints_clean("program.hlf")
assert_gas_under("program.hlf", max_gas=50)
```

### pytest Integration

```bash
python -m pytest --hlf-dir=hlf/stdlib/ -v
```

Automatically discovers and tests all `.hlf` files in the specified directory.
