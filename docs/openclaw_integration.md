# OpenClaw Integration

OpenClaw provides external binary tooling for document summarization and analysis within the Sovereign Agentic OS.

## Strategies

### Strategy B — Whitelisted Pure Tools
- Only SHA-256 verified binaries are permitted
- Gas cost: 5–10 per invocation
- Available on Tier 2 (forge) and Tier 3 (sovereign) only
- All `sensitive: true` host function returns are SHA-256 hashed before Merkle logging

### Strategy C — Sandbox Docker Compose Profile
- Tools run inside ephemeral Docker containers with strict resource caps
- `OLLAMA_ALLOW_OPENCLAW=1` is only permitted in development
- Resource caps: `memory=256M`, `pids_limit=50`, `network=none`
- CI guard automatically fails if `OLLAMA_ALLOW_OPENCLAW=1` is found in production `docker-compose.yml`

## OPENCLAW_SUMMARIZE Host Function

| Field | Value |
|-------|-------|
| Gas | 7 |
| Tiers | forge, sovereign |
| Sensitive | true |
| Backend | dapr_container_spawn |
| Binary | `/opt/openclaw/bin/openclaw_summarize` |

## Security Notes

- The `binary_sha256` field in `governance/host_functions.json` must be populated before deployment
- The binary must be verified at runtime before execution
- All outputs are hashed with SHA-256 before Merkle chain inclusion
