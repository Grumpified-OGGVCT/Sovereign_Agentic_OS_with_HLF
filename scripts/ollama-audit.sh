#!/usr/bin/env bash
# Ollama Audit — every 30s: export num_ctx as Prometheus gauge.
# Skip :cloud suffix models. Warn if local model exceeds MAX_CONTEXT_TOKENS.
set -euo pipefail

OLLAMA_HOST="${OLLAMA_HOST:-http://ollama-matrix:11434}"
MAX_CONTEXT_TOKENS="${MAX_CONTEXT_TOKENS:-8192}"

while true; do
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Running Ollama audit..."
    TAGS_JSON=$(curl -sf "${OLLAMA_HOST}/api/tags" || echo '{"models":[]}')
    MODELS=$(echo "$TAGS_JSON" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for m in data.get('models', []):
    print(m['name'])
" 2>/dev/null || true)

    for MODEL in $MODELS; do
        # Skip cloud models
        if [[ "$MODEL" == *":cloud" ]]; then
            continue
        fi

        INFO_JSON=$(curl -sf -X POST "${OLLAMA_HOST}/api/show" \
            -H 'Content-Type: application/json' \
            -d "{\"name\":\"${MODEL}\"}" || echo '{}')

        NUM_CTX=$(echo "$INFO_JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
params = d.get('parameters', '')
for line in str(params).splitlines():
    if 'num_ctx' in line:
        parts = line.split()
        if len(parts) >= 2:
            print(parts[-1])
            sys.exit(0)
print(0)
" 2>/dev/null || echo "0")

        echo "# HELP ollama_model_num_ctx Context window size for Ollama model"
        echo "# TYPE ollama_model_num_ctx gauge"
        echo "ollama_model_num_ctx{model=\"${MODEL}\"} ${NUM_CTX}"

        if [[ "$NUM_CTX" -gt "$MAX_CONTEXT_TOKENS" ]] 2>/dev/null; then
            echo "[WARN] Model ${MODEL} num_ctx=${NUM_CTX} exceeds MAX_CONTEXT_TOKENS=${MAX_CONTEXT_TOKENS}" >&2
        fi
    done

    sleep 30
done
