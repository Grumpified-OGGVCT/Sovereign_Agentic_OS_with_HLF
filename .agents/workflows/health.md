---
description: Check health of all Sovereign OS services
---

# Health Check Workflow

Verify all Sovereign OS services are online and responding.

## Steps

// turbo-all

1. Check Gateway Bus:
```bash
curl -sf http://localhost:40404/health && echo "✅ Gateway OK" || echo "❌ Gateway DOWN"
```

2. Check Ollama:
```bash
curl -sf http://localhost:11434/api/tags | python -c "import sys,json; m=json.load(sys.stdin).get('models',[]); print(f'✅ Ollama OK — {len(m)} models')" 2>/dev/null || echo "❌ Ollama DOWN"
```

3. Check Redis:
```bash
python -c "import redis; r=redis.Redis(); r.ping(); print('✅ Redis OK')" 2>/dev/null || echo "❌ Redis DOWN"
```

4. Check Memory DB:
```bash
python -c "
import sqlite3, os
db='data/sqlite/memory.db'
if os.path.exists(db):
    c=sqlite3.connect(db)
    facts=c.execute('SELECT COUNT(*) FROM fact_store').fetchone()[0]
    print(f'✅ Memory DB OK — {facts} facts')
    c.close()
else:
    print('❌ Memory DB not found')
"
```

5. Check Streamlit GUI:
```bash
curl -sf http://localhost:8501 > /dev/null && echo "✅ Streamlit GUI OK" || echo "❌ Streamlit GUI DOWN"
```
