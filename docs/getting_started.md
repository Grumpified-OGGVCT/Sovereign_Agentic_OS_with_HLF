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
```

## Architecture

See `README.md` for the full architecture diagram.
