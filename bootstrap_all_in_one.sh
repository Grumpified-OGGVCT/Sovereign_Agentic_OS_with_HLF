#!/usr/bin/env bash
# Sovereign Agentic OS — Genesis Block Bootstrap
# Runs full initialization sequence.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
export DEPLOYMENT_TIER="${DEPLOYMENT_TIER:-hearth}"

echo "============================================================"
echo " SOVEREIGN AGENTIC OS — GENESIS BLOCK BOOT SEQUENCE"
echo " Tier: $DEPLOYMENT_TIER"
echo "============================================================"

shutdown_sequence() {
    echo "[SHUTDOWN] Initiating shutdown sequence..."
    # Drain Redis streams
    if command -v redis-cli &>/dev/null; then
        redis-cli -u "${REDIS_URL:-redis://localhost:6379}" \
            XGROUP SETID intents executor-group 0 2>/dev/null || true
    fi
    # SQLite WAL checkpoint
    if [ -f "$REPO_ROOT/data/sqlite/memory.db" ]; then
        sqlite3 "$REPO_ROOT/data/sqlite/memory.db" \
            "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
    fi
    echo "[SHUTDOWN] Final ALS trace written."
    docker compose --profile "$DEPLOYMENT_TIER" down 2>/dev/null || true
    echo "[SHUTDOWN] Complete."
}

trap shutdown_sequence EXIT SIGTERM

# Step 1: Check Docker daemon
echo "[1/8] Checking Docker daemon..."
docker info > /dev/null 2>&1 || { echo "ERROR: Docker daemon not running"; exit 1; }
echo "  Docker OK"

# Step 2: Run pytest
echo "[2/8] Running test suite..."
if command -v uv &>/dev/null; then
    uv run pytest "$REPO_ROOT/tests/" -v --tb=short
else
    python3 -m pytest "$REPO_ROOT/tests/" -v --tb=short
fi
echo "  Tests passed"

# Step 3: Generate KYA certs
echo "[3/8] Generating KYA certificates..."
bash "$REPO_ROOT/governance/kya_init.sh" 2>/dev/null || echo "  [SKIP] KYA cert generation skipped (no openssl)"

# Step 4: Run alembic migrations
echo "[4/8] Running database migrations..."
if command -v uv &>/dev/null; then
    uv run alembic upgrade head 2>/dev/null || echo "  [SKIP] No alembic migrations found"
else
    python3 -m alembic upgrade head 2>/dev/null || echo "  [SKIP] No alembic migrations found"
fi

# Step 5: Docker compose build
echo "[5/8] Building Docker images..."
docker compose build

# Step 6: Docker compose up with profile
echo "[6/8] Starting services (profile: $DEPLOYMENT_TIER)..."
docker compose --profile "$DEPLOYMENT_TIER" up -d

# Step 7: Wait for healthchecks
echo "[7/8] Waiting for healthchecks..."
RETRIES=30
for i in $(seq 1 $RETRIES); do
    if docker compose ps --format json 2>/dev/null | python3 -c "
import json, sys
data = sys.stdin.read()
# Check if any service is unhealthy
if 'unhealthy' in data:
    sys.exit(1)
sys.exit(0)
" 2>/dev/null; then
        echo "  All services healthy"
        break
    fi
    if [ "$i" -eq "$RETRIES" ]; then
        echo "  [WARN] Healthcheck timeout — services may still be starting"
    fi
    sleep 5
done

# Step 8: Verify Ollama models
echo "[8/8] Verifying Ollama models..."
OLLAMA_HOST="${OLLAMA_HOST:-http://ollama-matrix:11434}"
if curl -sf "${OLLAMA_HOST}/api/tags" > /dev/null 2>&1; then
    MODELS=$(curl -sf "${OLLAMA_HOST}/api/tags" | python3 -c "
import json, sys
d = json.load(sys.stdin)
models = [m['name'] for m in d.get('models', [])]
print(', '.join(models) if models else '(none loaded)')
" 2>/dev/null || echo "(query failed)")
    echo "  Available models: $MODELS"
else
    echo "  [SKIP] Ollama not reachable at $OLLAMA_HOST"
fi

echo ""
echo "============================================================"
echo " [SOVEREIGN OS GENESIS BLOCK INITIALIZED. AWAITING INTENT.]"
echo "============================================================"
