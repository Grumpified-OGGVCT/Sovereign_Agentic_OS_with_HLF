"""
Model-Agnostic Policy Lint & Anti-Devolution Guard.

This script runs in CI to enforce two critical policies:
1. No hardcoded external model names in code/docs (model-agnostic policy)
2. No code quality regression (anti-devolution guard)

Exit code 0 = pass, 1 = violations found.
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

# Fix Windows console encoding
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent

# ─── Model-Agnostic Policy ──────────────────────────────────────────────
# These patterns indicate someone hardcoded a specific external model name
# as if this project USES it. Comparison/documentation references are OK
# when they appear in clearly comparative context.
BANNED_MODEL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("GPT-4o/GPT-4/GPT-3.5", re.compile(r"(?i)\bGPT[-‑]?[34][o]?\b")),
    ("Claude/Anthropic model", re.compile(r"(?i)\bclaude[-‑]?\d?\b")),
    ("DeepSeek model", re.compile(r"(?i)\bdeepseek[-‑]v?\d?\b")),
    ("Llama model", re.compile(r"(?i)\bllama[-‑]?\d\b")),
    ("Mistral model", re.compile(r"(?i)\bmistral[-‑]?\d?\b")),
]

# Files/dirs to SKIP (legitimate references — these files need to name all models)
SKIP_PATHS: set[str] = {
    ".github/copilot-instructions.md",  # Policy definitions themselves
    "CONTRIBUTING.md",  # Policy documentation
    "scripts/model_policy_lint.py",  # This file
    "docs/Automated_Runner_Setup_Guide.md",  # Existing cloud provider docs
    "Sovereign_OS_Master_Build_Plan.md",  # Legacy plan (read-only reference)
    "TODO.md",  # Task tracking (references completed work)
}

# Directories where ALL model names are legitimate (benchmarking, testing, registry)
SKIP_DIR_PREFIXES: list[str] = [
    "tests/",  # Test fixtures use real model names
    "agents/gateway/matrix_sync/",  # Model registry — its JOB is to track all models
]

# File extensions to scan
SCAN_EXTENSIONS: set[str] = {".py", ".md", ".yml", ".yaml", ".json", ".toml", ".hlf", ".sh", ".bat", ".ps1"}

# Context patterns that make a match OK (comparative/negative/listing references)
SAFE_CONTEXT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)(unlike|compared to|instead of|rather than|vs\.?)"),
    re.compile(r"(?i)(banned|do not|never|❌|BAD|rejects?:?)"),
    re.compile(r"(?i)(staged|future|integrating|routing logic)"),
    re.compile(r"(?i)(copilot|cursor|aider|jules|antigravity)"),  # Lists of agents/tools
    re.compile(r"^\s*#"),  # Python comments
    re.compile(r'"""'),  # Docstrings
    re.compile(r"'''"),  # Docstrings
    re.compile(r"\[x\]"),  # Completed TODO items
    re.compile(r"--\s*(e\.g\.|example|like)"),  # SQL DDL comments with examples
    re.compile(r"(?i)\be\.g\.\s"),  # "e.g." anywhere
]


def is_safe_context(line: str) -> bool:
    """Check if the line uses the model name in a comparative/negative context."""
    return any(p.search(line) for p in SAFE_CONTEXT_PATTERNS)


def scan_model_policy() -> list[str]:
    """Scan repo for banned model name hardcoding. Returns list of violations."""
    violations: list[str] = []

    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in SCAN_EXTENSIONS:
            continue
        rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        if any(rel.startswith(skip) or rel == skip for skip in SKIP_PATHS):
            continue
        if any(rel.startswith(prefix) for prefix in SKIP_DIR_PREFIXES):
            continue
        # Skip hidden dirs (except .github workflows)
        parts = path.relative_to(REPO_ROOT).parts
        if any(p.startswith(".") and p != ".github" for p in parts):
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue

        for line_num, line in enumerate(content.splitlines(), 1):
            for name, pattern in BANNED_MODEL_PATTERNS:
                if pattern.search(line) and not is_safe_context(line):
                    violations.append(f"  {rel}:{line_num} — {name} reference: {line.strip()[:120]}")

    return violations


# ─── Anti-Devolution Guard ───────────────────────────────────────────────


def scan_anti_devolution() -> list[str]:
    """Check for common code quality regressions."""
    violations: list[str] = []

    # Check: No os.system / subprocess.call / eval / exec in agent code
    # Note: r.eval() (Redis), self.eval(), model.eval() are NOT banned — only bare eval()
    BANNED_IMPORTS = re.compile(
        r"(?<!#)(?<!\.)\b(os\.system|subprocess\.call)\b|"
        r"(?<!\.)(?<!\w)\beval\s*\(|"
        r"(?<!\.)(?<!\w)\bexec\s*\("
    )
    agent_dirs = [REPO_ROOT / "agents", REPO_ROOT / "hlf", REPO_ROOT / "mcp"]

    for agent_dir in agent_dirs:
        if not agent_dir.exists():
            continue
        for path in agent_dir.rglob("*.py"):
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue
            rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
            in_docstring = False
            for line_num, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                # Track docstring boundaries (triple quotes toggle state)
                if '"""' in stripped or "'''" in stripped:
                    # Count occurrences — odd count toggles state
                    dq = stripped.count('"""')
                    sq = stripped.count("'''")
                    if (dq + sq) % 2 == 1:
                        in_docstring = not in_docstring
                    continue  # Skip the boundary line itself
                if in_docstring:
                    continue
                # Skip comments — they document what's banned, not use it
                if stripped.startswith("#"):
                    continue
                if BANNED_IMPORTS.search(line):
                    violations.append(f"  {rel}:{line_num} — Banned import/call: {line.strip()[:120]}")

    # Check: ALIGN_LEDGER.yaml must exist and not be empty
    align_path = REPO_ROOT / "governance" / "ALIGN_LEDGER.yaml"
    if not align_path.exists():
        violations.append("  governance/ALIGN_LEDGER.yaml is MISSING — security policy deleted!")
    elif align_path.stat().st_size < 100:
        violations.append("  governance/ALIGN_LEDGER.yaml is suspiciously small — rules may have been deleted!")

    # Check: seccomp.json must exist
    seccomp_path = REPO_ROOT / "security" / "seccomp.json"
    if not seccomp_path.exists():
        violations.append("  security/seccomp.json is MISSING — container security deleted!")

    # Check: settings.json must have ollama_allowed_models
    settings_path = REPO_ROOT / "config" / "settings.json"
    if settings_path.exists():
        settings_content = settings_path.read_text(encoding="utf-8", errors="ignore")
        if "ollama_allowed_models" not in settings_content:
            violations.append("  config/settings.json missing ollama_allowed_models — model matrix deleted!")

    return violations


def main() -> int:
    print("═══════════════════════════════════════════════════════════")
    print(" MODEL-AGNOSTIC POLICY & ANTI-DEVOLUTION GUARD")
    print("═══════════════════════════════════════════════════════════")

    model_violations = scan_model_policy()
    devolution_violations = scan_anti_devolution()

    if model_violations:
        print(f"\n❌ MODEL POLICY VIOLATIONS ({len(model_violations)}):")
        print("  Hardcoded external model names found. Use config/settings.json model matrix.\n")
        for v in model_violations:
            print(v)

    if devolution_violations:
        print(f"\n❌ ANTI-DEVOLUTION VIOLATIONS ({len(devolution_violations)}):")
        print("  Code quality regression detected.\n")
        for v in devolution_violations:
            print(v)

    total = len(model_violations) + len(devolution_violations)
    if total == 0:
        print("\n✅ All checks passed — no policy violations detected.")
        return 0
    else:
        print(f"\n❌ {total} total violation(s) found. Fix before merging.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
