# README Update Instructions

> **What happened:** `README.md` was accidentally corrupted during the PR process — the full ~640-line file was overwritten with 2 lines. The README must be restored to the `main` branch version before applying the update below.

## Step 1: Restore README.md

Revert `README.md` to the current `main` branch version (all ~640 lines).

## Step 2: Apply Status-Line Update

Find the line containing:
```
> **⏸️ Paused**: Pure cloud-only orchestrations, LOLLMS integration (stretch goal).
```

Replace it with:
```
> **⏸️ Paused**: Pure cloud-only orchestrations.
> **🔄 Planned**: [Unified Ecosystem Integration](docs/UNIFIED_ECOSYSTEM_ROADMAP.md) — LOLLMS, MSTY Studio, AnythingLLM, and user repos as HLF host functions via `CALL_HOST` opcode through the 6-gate pipeline.
```

> **Note:** Use content-based search (the `⏸️ Paused` pattern) rather than line numbers, as line numbers may drift during restoration or rebasing.

This change will be applied in the next session along with additional README updates for new infographics and media.
