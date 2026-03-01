"""
HLF Codebase Metrics Scanner.

Scans the HLF toolchain source code and produces a JSON metrics file at
docs/metrics.json.  This gives Jules agents, the GitHub Pages dashboard,
and human contributors a single source of truth for language maturity.

Usage:
    python scripts/hlf_metrics.py            # prints to stdout
    python scripts/hlf_metrics.py --output docs/metrics.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
HLF_DIR = ROOT / "hlf"
TESTS_DIR = ROOT / "tests"
FIXTURES_DIR = TESTS_DIR / "fixtures"
DICT_FILE = ROOT / "governance" / "templates" / "dictionary.json"
SYNTAXES_DIR = ROOT / "syntaxes"


def count_grammar_rules(hlfc_path: Path) -> dict:
    """Parse _GRAMMAR in hlfc.py to count statement and terminal types."""
    text = hlfc_path.read_text(encoding="utf-8")

    # Find the grammar string
    grammar_match = re.search(r'_GRAMMAR\s*=\s*r?"""(.*?)"""', text, re.DOTALL)
    if not grammar_match:
        return {"statement_types": 0, "terminal_types": 0, "ignored_glyphs": 0}

    grammar = grammar_match.group(1)

    # Statement types = non-terminal rules (lines like "    name:")
    # Lark grammars are indented, so match with optional leading whitespace
    stmt_rules = re.findall(r"^\s+(\w+)\s*:", grammar, re.MULTILINE)
    # Terminal types = ALL-UPPERCASE rules (TAG, IDENT, etc.)
    terminal_rules = [r for r in stmt_rules if r.isupper()]
    # Non-terminal = lowercase rules (start, line, tag_stmt, etc.)
    non_terminal_rules = [r for r in stmt_rules if not r.isupper()]

    # Ignored glyphs — count %ignore directives
    ignore_lines = re.findall(r"%ignore\s+.+", grammar)

    return {
        "statement_types": len(non_terminal_rules),
        "terminal_types": len(terminal_rules),
        "ignored_glyphs": len(ignore_lines),
        "grammar_rules": stmt_rules,
    }


def count_builtins(hlfrun_path: Path) -> dict:
    """Count built-in functions and host function stubs."""
    text = hlfrun_path.read_text(encoding="utf-8")

    # _BUILTIN_FUNCTIONS dict entries
    builtin_match = re.findall(r'"(\w+)":\s*_builtin_', text)

    # Host function stubs from docstring ACTION lines
    host_match = re.findall(r"\[ACTION\]\s+(\w+)", text)

    return {
        "builtin_functions": len(builtin_match),
        "builtin_names": builtin_match,
        "host_function_stubs": len(host_match),
        "host_function_names": host_match,
    }


def count_toolchain_lines() -> dict:
    """Count lines of code in the HLF toolchain."""
    results = {}
    total = 0
    for py_file in sorted(HLF_DIR.glob("*.py")):
        lines = len(py_file.read_text(encoding="utf-8").splitlines())
        results[py_file.name] = lines
        total += lines
    return {"files": results, "total_lines": total}


def count_tests() -> dict:
    """Count test files and test functions."""
    test_files = list(TESTS_DIR.glob("test_*.py"))
    hlf_tests = 0
    total_functions = 0

    for tf in test_files:
        text = tf.read_text(encoding="utf-8")
        funcs = re.findall(r"def (test_\w+)", text)
        total_functions += len(funcs)
        if "hlf" in tf.name.lower():
            hlf_tests += len(funcs)

    # Try running pytest --co -q for accurate count
    pytest_count = total_functions
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--co", "-q"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=30,
        )
        if result.returncode == 0:
            # Last line is typically "N tests collected"
            for line in result.stdout.strip().splitlines():
                m = re.search(r"(\d+)\s+test", line)
                if m:
                    pytest_count = int(m.group(1))
    except Exception:
        pass

    return {
        "test_files": len(test_files),
        "total_tests_collected": pytest_count,
        "hlf_specific_tests": hlf_tests,
    }


def count_fixtures() -> dict:
    """Count .hlf fixture files."""
    fixtures = list(FIXTURES_DIR.glob("*.hlf"))
    return {
        "count": len(fixtures),
        "files": [f.name for f in sorted(fixtures)],
    }


def count_dictionary() -> dict:
    """Count tags and glyphs in dictionary.json."""
    if not DICT_FILE.exists():
        return {"tags": 0, "glyphs": 0}

    data = json.loads(DICT_FILE.read_text(encoding="utf-8"))
    tags = data.get("tags", [])
    glyphs = data.get("glyphs", {})

    return {
        "tag_count": len(tags) if isinstance(tags, list) else len(tags.keys()),
        "glyph_count": len(glyphs) if isinstance(glyphs, (list, dict)) else 0,
    }


def get_compiler_version() -> str:
    """Extract version from hlfc.py compile() output."""
    hlfc_text = (HLF_DIR / "hlfc.py").read_text(encoding="utf-8")
    m = re.search(r'"version":\s*"([\d.]+)"', hlfc_text)
    return m.group(1) if m else "unknown"


def get_syntax_scopes() -> int:
    """Count TextMate syntax scopes."""
    tmfile = SYNTAXES_DIR / "hlf.tmLanguage.json"
    if not tmfile.exists():
        return 0
    data = json.loads(tmfile.read_text(encoding="utf-8"))
    patterns = data.get("patterns", [])
    return len(patterns)


def main() -> None:
    parser = argparse.ArgumentParser(description="HLF Codebase Metrics Scanner")
    parser.add_argument("--output", "-o", help="Write JSON to file (default: stdout)")
    args = parser.parse_args()

    grammar = count_grammar_rules(HLF_DIR / "hlfc.py")
    builtins = count_builtins(HLF_DIR / "hlfrun.py")
    toolchain = count_toolchain_lines()
    tests = count_tests()
    fixtures = count_fixtures()
    dictionary = count_dictionary()

    metrics = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "compiler_version": get_compiler_version(),
        "grammar": {
            "statement_types": grammar["statement_types"],
            "terminal_types": grammar["terminal_types"],
            "ignored_glyphs": grammar["ignored_glyphs"],
        },
        "runtime": {
            "builtin_functions": builtins["builtin_functions"],
            "builtin_names": builtins["builtin_names"],
            "host_function_stubs": builtins["host_function_stubs"],
            "host_function_names": builtins["host_function_names"],
        },
        "toolchain": {
            "total_lines": toolchain["total_lines"],
            "files": toolchain["files"],
        },
        "tests": tests,
        "fixtures": fixtures,
        "dictionary": dictionary,
        "syntax_scopes": get_syntax_scopes(),
        "token_budgets": {
            "per_intent": 30,
            "per_file": 1500,
        },
    }

    output = json.dumps(metrics, indent=2)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"✅ Metrics written to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
