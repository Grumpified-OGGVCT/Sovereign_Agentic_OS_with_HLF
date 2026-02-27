#!/usr/bin/env bash
# Pre-compile governance/hls.yaml via Lark into a serialized Python parser module.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUTPUT="$REPO_ROOT/hlf/_parser_cache.py"

echo "[parser-build] Building Lark LALR parser from hls.yaml..."
uv run python3 - <<'EOF'
import sys
sys.path.insert(0, '.')
from hlf.hlfc import _parser
import pickle, base64

serialized = _parser.serialize()
encoded = base64.b64encode(serialized).decode()
output = f'# Auto-generated parser cache — do not edit manually\nPARSER_DATA = "{encoded}"\n'
with open("hlf/_parser_cache.py", "w") as f:
    f.write(output)
print("[parser-build] Parser cache written to hlf/_parser_cache.py")
EOF
