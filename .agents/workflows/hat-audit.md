---
description: Run a full Six Thinking Hats audit of the system
---

# Hat Audit Workflow

Run all six hats (Red, Black, White, Yellow, Green, Blue) against the current system state.

## Steps

1. Run the Hat audit via Dream Mode (which includes the Hat analysis stage):
```bash
uv run -c "
from agents.core.dream_state import run_dream_cycle
report = run_dream_cycle(manual=True)
print(f'Dream Cycle complete in {report.duration_seconds:.1f}s')
print(f'HLF Practice: {report.hlf_passed}/{report.hlf_practiced} passed')
for hr in report.hat_reports:
    hat = hr.get('hat', '?')
    count = hr.get('findings_count', 0)
    err = hr.get('error')
    status = f'⚠️ {err}' if err else f'{count} finding(s)' if count else '✅ Clean'
    print(f'  {hat.title():6s}: {status}')
print(f'Summary: {report.summary}')
"
```

2. View detailed findings from the database:
// turbo
```bash
uv run -c "
import sqlite3, json
conn = sqlite3.connect('data/sqlite/memory.db')
rows = conn.execute(
    'SELECT hat, severity, title, description, recommendation '
    'FROM hat_findings ORDER BY timestamp DESC LIMIT 20'
).fetchall()
for r in rows:
    print(f'[{r[0].upper():5s}] ({r[1]}) {r[2]}')
    print(f'  → {r[3][:120]}')
    if r[4]: print(f'  ✏️ {r[4][:120]}')
    print()
conn.close()
"
```

3. Review the findings in the GUI at Swarm State → 🌙 Dream Mode panel.
