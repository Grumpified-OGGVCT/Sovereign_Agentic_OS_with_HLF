#!/usr/bin/env bash
# ============================================================================
# jules_dispatch.sh — Issue → Jules Session Automation
# ============================================================================
# Dispatches a GitHub issue to a Jules session for autonomous resolution.
#
# Usage:
#   ./scripts/jules_dispatch.sh <issue-number> [--dry-run]
#   ./scripts/jules_dispatch.sh 42
#   ./scripts/jules_dispatch.sh 42 --dry-run
#
# Prerequisites:
#   - Jules CLI: npm install -g @google/jules
#   - JULES_API_KEY in .env or environment
#   - gh CLI authenticated (for issue fetching)
#
# What it does:
#   1. Fetches the issue title, body, and labels from GitHub
#   2. Maps labels to Jules task templates (CoVE, Eleven Hats, etc.)
#   3. Constructs a Jules prompt with invariants and context
#   4. Launches a Jules session with the prompt
#   5. Monitors the session and reports back to the issue
# ============================================================================

set -euo pipefail

# ─── Configuration ─────────────────────────────────────────────────
REPO="Grumpified-OGGVCT/Sovereign_Agentic_OS_with_HLF"
GOVERNANCE_DIR="governance/templates"
CONFIG_FILE="config/jules_tasks.yaml"
REPORTS_DIR="reports"

# Load API key from .env if not already set
if [[ -z "${JULES_API_KEY:-}" ]]; then
    if [[ -f .env ]]; then
        export "$(grep JULES_API_KEY .env | xargs)"
    fi
fi

if [[ -z "${JULES_API_KEY:-}" ]]; then
    echo "❌ JULES_API_KEY not found in environment or .env"
    exit 1
fi

# ─── Arguments ─────────────────────────────────────────────────────
ISSUE_NUMBER="${1:?Usage: jules_dispatch.sh <issue-number> [--dry-run]}"
DRY_RUN="${2:-}"

# ─── Fetch Issue from GitHub ───────────────────────────────────────
echo "📋 Fetching issue #${ISSUE_NUMBER} from ${REPO}..."
ISSUE_JSON=$(gh issue view "$ISSUE_NUMBER" --repo "$REPO" --json title,body,labels 2>/dev/null || echo '{}')

if [[ "$ISSUE_JSON" == "{}" ]]; then
    echo "❌ Could not fetch issue #${ISSUE_NUMBER}. Is gh authenticated?"
    exit 1
fi

ISSUE_TITLE=$(echo "$ISSUE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('title',''))")
ISSUE_BODY=$(echo "$ISSUE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('body',''))")
ISSUE_LABELS=$(echo "$ISSUE_JSON" | python3 -c "import sys,json; print(','.join(l['name'] for l in json.load(sys.stdin).get('labels',[])))")

echo "  Title:  ${ISSUE_TITLE}"
echo "  Labels: ${ISSUE_LABELS:-none}"

# ─── Map Labels to Validation Templates ────────────────────────────
VALIDATION_TEMPLATE=""
if echo "$ISSUE_LABELS" | grep -qi "security"; then
    VALIDATION_TEMPLATE="${GOVERNANCE_DIR}/cove_full_validation.md"
    echo "  🛡️  Security label → Full CoVE validation required"
elif echo "$ISSUE_LABELS" | grep -qi "hlf\|grammar\|compiler"; then
    VALIDATION_TEMPLATE="${GOVERNANCE_DIR}/cove_compact_validation.md"
    echo "  📝 HLF label → Compact CoVE validation"
elif echo "$ISSUE_LABELS" | grep -qi "gui\|ui\|frontend"; then
    VALIDATION_TEMPLATE="${GOVERNANCE_DIR}/cove_compact_validation.md"
    echo "  🖥️  UI label → Compact CoVE validation"
fi

# ─── Construct Jules Prompt ────────────────────────────────────────
INVARIANTS=$(cat <<'EOF'
## Sovereign OS Invariants (NEVER VIOLATE)
1. No test deletion — test count must be >= baseline
2. No coverage reduction — coverage must be >= baseline
3. No simplification — all existing features preserved
4. Additive-only — new code alongside existing, never replacing
5. 4GB RAM constraint — Layer 1 ACFS compliance
6. Cloud isolation — local models NEVER in cloud tier walk
7. Gas enforcement — every route consumes gas via consume_gas_async()
8. ALIGN enforcement — all outputs pass through enforce_align()
9. Merkle-chain — all ALS logs chain via ALSLogger.log()
EOF
)

PROMPT=$(cat <<EOF
You are working on the Sovereign Agentic OS with HLF repository.

## Issue #${ISSUE_NUMBER}: ${ISSUE_TITLE}

${ISSUE_BODY}

${INVARIANTS}

## Instructions
1. Read AGENTS.md for repository context
2. Understand the existing code before making changes
3. Make only ADDITIVE changes — never delete or simplify existing code
4. Run the full test suite: uv run python -m pytest tests/ -v
5. Ensure all 132+ tests pass before creating a PR
6. Apply the Eleven Hats Review protocol from ${GOVERNANCE_DIR}/eleven_hats_review.md
EOF
)

if [[ -n "$VALIDATION_TEMPLATE" ]]; then
    PROMPT+=$'\n\n'"7. Run validation template: ${VALIDATION_TEMPLATE}"
fi

# ─── Execute or Dry Run ───────────────────────────────────────────
if [[ "$DRY_RUN" == "--dry-run" ]]; then
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "  DRY RUN — Would dispatch to Jules with this prompt:"
    echo "═══════════════════════════════════════════════════════════"
    echo ""
    echo "$PROMPT"
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "  Validation template: ${VALIDATION_TEMPLATE:-none}"
    echo "═══════════════════════════════════════════════════════════"
    exit 0
fi

# Create reports directory if it doesn't exist
mkdir -p "$REPORTS_DIR"

echo ""
echo "🚀 Dispatching issue #${ISSUE_NUMBER} to Jules..."
echo ""

# Launch Jules session
jules task \
    --prompt "$PROMPT" \
    --repo "$REPO" \
    --branch "jules/issue-${ISSUE_NUMBER}" \
    2>&1 | tee "${REPORTS_DIR}/jules-issue-${ISSUE_NUMBER}.log"

echo ""
echo "✅ Jules session complete for issue #${ISSUE_NUMBER}"
echo "   Log: ${REPORTS_DIR}/jules-issue-${ISSUE_NUMBER}.log"
