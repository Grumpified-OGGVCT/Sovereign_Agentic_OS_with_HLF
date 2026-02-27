#!/usr/bin/env bash
# Snapshot Merkle — compute SHA-256 Merkle root of all governance/ files.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GOVERNANCE_DIR="$REPO_ROOT/governance"

echo "[snapshot_merkle] Computing Merkle root of governance/ files..."
HASHES=()
while IFS= read -r -d '' file; do
    HASH=$(sha256sum "$file" | awk '{print $1}')
    HASHES+=("$HASH")
    echo "  $HASH  $file"
done < <(find "$GOVERNANCE_DIR" -type f -print0 | sort -z)

if [ ${#HASHES[@]} -eq 0 ]; then
    echo "[snapshot_merkle] No files found in governance/"
    exit 1
fi

# Compute Merkle root: iteratively hash pairs
LEVEL=("${HASHES[@]}")
while [ ${#LEVEL[@]} -gt 1 ]; do
    NEXT=()
    for ((i=0; i<${#LEVEL[@]}; i+=2)); do
        if [ $((i+1)) -lt ${#LEVEL[@]} ]; then
            COMBINED="${LEVEL[$i]}${LEVEL[$((i+1))]}"
        else
            COMBINED="${LEVEL[$i]}${LEVEL[$i]}"
        fi
        NEXT+=("$(echo -n "$COMBINED" | sha256sum | awk '{print $1}')")
    done
    LEVEL=("${NEXT[@]}")
done

echo "[snapshot_merkle] Merkle root: ${LEVEL[0]}"
