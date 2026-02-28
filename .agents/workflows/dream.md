---
description: Trigger a Dream Mode cycle with full Hat analysis
---

# Dream Mode Workflow

Run the full 5-stage Dream Mode pipeline manually.

## Steps

1. Check that Ollama is running and the analysis model is available:
// turbo
```bash
curl -s http://localhost:11434/api/tags | python -c "import sys,json; print(len(json.load(sys.stdin).get('models',[])), 'models available')"
```

2. Check that the Gateway Bus is online:
// turbo
```bash
curl -s http://localhost:40404/health
```

3. Run the Dream Mode cycle:
```bash
uv run -c "from agents.core.dream_state import run_dream_cycle; r = run_dream_cycle(manual=True); print(f'Duration: {r.duration_seconds:.1f}s | HLF: {r.hlf_passed}/{r.hlf_practiced} | Summary: {r.summary}')"
```

4. Review Hat findings in the GUI → Swarm State → Dream Mode panel, or query directly:
// turbo
```bash
uv run -c "
import sqlite3, json
conn = sqlite3.connect('data/sqlite/memory.db')
rows = conn.execute('SELECT hat, severity, title FROM hat_findings ORDER BY timestamp DESC LIMIT 10').fetchall()
for r in rows: print(f'{r[0]:6s} | {r[1]:8s} | {r[2]}')
conn.close()
"
```
