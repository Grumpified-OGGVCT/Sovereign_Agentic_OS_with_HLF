"""
HLF vs NLP Compression Benchmark.

Compares equivalent instructions written in HLF and natural language / JSON
to produce real, measured token compression ratios.

Each benchmark case contains:
  - A natural language description of the agent task (what a user would type)
  - The equivalent full JSON/NLP payload (what the agent would receive without HLF)
  - The HLF encoding of the same instruction
  - Real token counts via tiktoken cl100k_base (when available) or BPE estimate

Usage:
    python scripts/hlf_benchmark.py
    python scripts/hlf_benchmark.py --output docs/benchmark.json
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "tests" / "fixtures"

# ── Tokenizer ──────────────────────────────────────────────────────

_tokenizer = None
_tokenizer_name = "bpe_estimate"


def _load_tokenizer():
    """Try tiktoken first, fall back to BPE estimate."""
    global _tokenizer, _tokenizer_name
    try:
        import tiktoken
        _tokenizer = tiktoken.get_encoding("cl100k_base")
        _tokenizer_name = "tiktoken_cl100k_base"
    except ImportError:
        _tokenizer = None
        _tokenizer_name = "bpe_estimate"


def count_tokens(text: str) -> int:
    """Count tokens using tiktoken or BPE approximation."""
    if _tokenizer is not None:
        return len(_tokenizer.encode(text))
    # BPE approximation: ~4 chars per token for English
    return max(1, len(text.strip()) // 4)


# ── Benchmark Cases ────────────────────────────────────────────────
# Each case has:
#   name: human label
#   domain: category
#   nlp_payload: the full JSON/NLP instruction an agent would receive
#   hlf_file: path to .hlf fixture (if available)
#   hlf_inline: inline HLF (fallback if no fixture)

BENCHMARK_CASES = [
    {
        "name": "Security Audit",
        "domain": "DevOps",
        "nlp_payload": (
            '{"task": "analyze", "target": "/security/seccomp.json", '
            '"mode": "read-only", "expected_output": "vulnerability_shorthand", '
            '"voting": {"consensus": "strict"}, '
            '"agent_instructions": "Scan the seccomp configuration file for any '
            "CVE vulnerabilities. Access is read-only. Return a compact shorthand "
            "summary of findings. All participating agents must agree on the result "
            'before it is finalized.", '
            '"result_format": {"code": 0, "message": "scan_complete"}}'
        ),
        "hlf_file": "seccomp_audit.hlf",
    },
    {
        "name": "Content Delegation",
        "domain": "Creative",
        "nlp_payload": (
            '{"task": "delegate", "target_agent": "scribe", '
            '"goal": "fractal_summarize", "source": "/data/raw_research.txt", '
            '"priority": "high", "constraints": {"vram_limit": "8GB"}, '
            '"agent_instructions": "Delegate a fractal summarization task to the '
            "Scribe agent. Use the raw research file as input. This is high "
            "priority and should respect the 8GB VRAM allocation limit for the "
            'local model.", '
            '"result_format": {"code": 0, "message": "delegated"}}'
        ),
        "hlf_file": "creative_delegate.hlf",
    },
    {
        "name": "Stack Deployment",
        "domain": "Architecture",
        "nlp_payload": (
            '{"task": "deploy", "stack": "sovereign-prod", "replicas": 3, '
            '"tier": "forge", "health_check": true, '
            '"rollback_on_fail": true, "priority": "urgent", '
            '"agent_instructions": "Deploy the sovereign-prod stack with 3 '
            "replicas on the Forge tier. Enable health checks and automatically "
            "roll back if deployment fails. This is an urgent priority "
            'deployment.", '
            '"result_format": {"code": 0, "message": "deploy_initiated"}}'
        ),
        "hlf_file": "deploy_stack.hlf",
    },
    {
        "name": "Database Migration",
        "domain": "Data",
        "nlp_payload": (
            '{"task": "migrate", "database": "user_profiles", '
            '"target_version": "v2.3", "backup_first": true, '
            '"max_downtime": "30s", "dry_run": false, "priority": "high", '
            '"integrity_check": "sha256_hash_of_db_and_version", '
            '"agent_instructions": "Run the database migration for user_profiles '
            "to version v2.3. Create a backup before migrating. Maximum allowed "
            "downtime is 30 seconds. This is not a dry run. Compute a SHA-256 "
            'hash of the database name and version for integrity verification.", '
            '"result_format": {"code": 0, "message": "migration_complete"}}'
        ),
        "hlf_file": "db_migration.hlf",
    },
    {
        "name": "Log Analysis",
        "domain": "Observability",
        "nlp_payload": (
            '{"task": "summarize", "agent": "scribe", '
            '"source": "/logs/agent_activity_latest.log", '
            '"timespan": "24h", "max_tokens": 2048, '
            '"priority": "medium", "voting": {"consensus": "quorum"}, '
            '"agent_instructions": "Delegate log summarization to the Scribe '
            "agent. Analyze the latest agent activity log for the past 24 hours. "
            "Keep the summary within 2048 tokens. Use quorum consensus — a "
            'majority of agents must agree on the summary.", '
            '"result_format": {"code": 0, "message": "summary_ready"}}'
        ),
        "hlf_file": "log_analysis.hlf",
    },
    {
        "name": "Hello World",
        "domain": "Baseline",
        "nlp_payload": (
            '{"task": "greet", "target": "world", "message": "Hello, World!", '
            '"agent_instructions": "Execute a simple greeting intent towards the '
            "world entity. Emit a friendly hello message. This is the canonical "
            'baseline test for HLF compilation.", '
            '"result_format": {"code": 0, "message": "greeting_sent"}}'
        ),
        "hlf_file": "hello_world.hlf",
    },
]


def run_benchmark() -> dict:
    """Run all benchmark cases and return results."""
    _load_tokenizer()

    results = []
    total_nlp = 0
    total_hlf = 0

    for case in BENCHMARK_CASES:
        # Get HLF text
        hlf_text = ""
        hlf_source = "inline"
        if case.get("hlf_file"):
            hlf_path = FIXTURES_DIR / case["hlf_file"]
            if hlf_path.exists():
                hlf_text = hlf_path.read_text(encoding="utf-8").strip()
                hlf_source = str(hlf_path.relative_to(ROOT))
        if not hlf_text and case.get("hlf_inline"):
            hlf_text = case["hlf_inline"]

        nlp_tokens = count_tokens(case["nlp_payload"])
        hlf_tokens = count_tokens(hlf_text) if hlf_text else 0

        compression = 0.0
        if nlp_tokens > 0 and hlf_tokens > 0:
            compression = round((1 - hlf_tokens / nlp_tokens) * 100, 1)

        total_nlp += nlp_tokens
        total_hlf += hlf_tokens

        results.append({
            "name": case["name"],
            "domain": case["domain"],
            "nlp_tokens": nlp_tokens,
            "hlf_tokens": hlf_tokens,
            "compression_pct": compression,
            "tokens_saved": nlp_tokens - hlf_tokens,
            "swarm_5_saved": (nlp_tokens - hlf_tokens) * 5,
            "hlf_source": hlf_source,
        })

    overall_compression = round((1 - total_hlf / total_nlp) * 100, 1) if total_nlp > 0 else 0

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "tokenizer": _tokenizer_name,
        "case_count": len(results),
        "overall": {
            "total_nlp_tokens": total_nlp,
            "total_hlf_tokens": total_hlf,
            "overall_compression_pct": overall_compression,
            "total_saved": total_nlp - total_hlf,
            "swarm_5_total_saved": (total_nlp - total_hlf) * 5,
        },
        "cases": results,
    }


def print_table(benchmark: dict) -> None:
    """Pretty-print the benchmark results."""
    cases = benchmark["cases"]
    overall = benchmark["overall"]

    print(f"\n{'=' * 78}")
    print(f"  HLF vs NLP Compression Benchmark  ({benchmark['tokenizer']})")
    print(f"  Generated: {benchmark['generated_at']}")
    print(f"{'=' * 78}\n")

    header = f"{'Task':<20} {'Domain':<14} {'NLP':>6} {'HLF':>6} {'Saved':>6} {'Comp%':>7} {'5-Agent':>8}"
    print(header)
    print("-" * len(header))

    for c in cases:
        print(
            f"{c['name']:<20} {c['domain']:<14} "
            f"{c['nlp_tokens']:>6} {c['hlf_tokens']:>6} "
            f"{c['tokens_saved']:>6} {c['compression_pct']:>6.1f}% "
            f"{c['swarm_5_saved']:>8}"
        )

    print("-" * len(header))
    print(
        f"{'TOTAL':<20} {'':>14} "
        f"{overall['total_nlp_tokens']:>6} {overall['total_hlf_tokens']:>6} "
        f"{overall['total_saved']:>6} {overall['overall_compression_pct']:>6.1f}% "
        f"{overall['swarm_5_total_saved']:>8}"
    )
    print(f"\n🐝 In a 5-agent swarm: {overall['swarm_5_total_saved']} tokens saved per broadcast\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="HLF vs NLP Compression Benchmark")
    parser.add_argument("--output", "-o", help="Write JSON to file")
    parser.add_argument("--quiet", "-q", action="store_true", help="Skip table output")
    args = parser.parse_args()

    benchmark = run_benchmark()

    if not args.quiet:
        print_table(benchmark)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(benchmark, indent=2), encoding="utf-8"
        )
        print(f"✅ Benchmark written to {args.output}")


if __name__ == "__main__":
    main()
