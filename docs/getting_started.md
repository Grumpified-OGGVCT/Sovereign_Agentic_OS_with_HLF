# Getting Started

## Prerequisites

- Docker 24+
- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager

## Quick Start

```bash
cp .env.example .env
bash bootstrap_all_in_one.sh
```

## Deployment Tiers

| Tier | Profile | Gas Bucket | Context Tokens |
|------|---------|------------|---------------|
| hearth | (default) | 1,000 | 8,192 |
| forge | forge | 10,000 | 16,384 |
| sovereign | sovereign | 100,000 | 32,768 |

Set `DEPLOYMENT_TIER` in your `.env` file before running.

## HLF Quick Reference

```hlf
[HLF-v2]
[INTENT] analyze /security/seccomp.json
[CONSTRAINT] mode="read-only"
[EXPECT] vulnerability_report
Ω
```

## Development Setup

```bash
uv sync
uv run pytest tests/ -v
uv run hlfc tests/fixtures/hello_world.hlf
uv run hlflint tests/fixtures/hello_world.hlf
uv run hlfsh               # Interactive HLF REPL
```

## HLF Toolchain

| Tool | Command | Purpose |
|------|---------|---------|
| Compiler | `uv run hlfc <file.hlf>` | Compile HLF to JSON AST / `.hlb` bytecode |
| Formatter | `uv run hlffmt <file.hlf>` | Canonical formatting with `--in-place` flag |
| Linter | `uv run hlflint <file.hlf>` | Token budget, gas limits, unused vars |
| Runtime | `uv run hlfrun <file.hlf>` | Execute compiled HLF bytecode |
| Shell | `uv run hlfsh` | Interactive REPL with history |
| Test Runner | `uv run hlftest <dir>` | HLF-native test suite with CoVE validation |
| Package Mgr | `uv run hlfpm install <pkg>` | OCI-based module install/update/freeze |
| Language Server | `uv run hlflsp` | LSP 3.17 for IDE diagnostics + completions |

## Architecture

See `README.md` for the full architecture diagram and agent layer reference.

## Key Docs

| Document | Path |
|----------|------|
| HLF Grammar Reference | `docs/HLF_GRAMMAR_REFERENCE.md` |
| HLF Progress Report | `docs/HLF_PROGRESS.md` |
| RFC 9000 Series | `docs/RFC_9000_SERIES.md` |
| CLI Tools | `docs/cli-tools.md` |
| Standard Library | `docs/stdlib.md` |
| OpenClaw Integration | `docs/openclaw_integration.md` |
| Language Reference | `docs/language-reference.md` |
| Automated Runner Guide | `docs/Automated_Runner_Setup_Guide.md` |
