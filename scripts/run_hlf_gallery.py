#!/usr/bin/env python3
"""
HLF Gallery Runner — Compiles and reports on all HLF programs.

Usage:
    python scripts/run_hlf_gallery.py

Discovers all .hlf files in hlf_programs/, compiles each via hlfc,
optionally runs via the bytecode VM, then generates markdown reports
and updates the gallery index.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Add project root to path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from hlf.hlfc import compile as hlfc_compile  # noqa: E402
from hlf.bytecode import BytecodeCompiler  # noqa: E402


def discover_programs(base_dir: Path) -> list[Path]:
    """Find all .hlf files in the programs directory."""
    return sorted(base_dir.glob("*.hlf"))


def compile_program(path: Path) -> dict:
    """Compile a single HLF program and return a report dict."""
    source = path.read_text(encoding="utf-8")
    report = {
        "name": path.stem,
        "file": path.name,
        "source_lines": len(source.strip().splitlines()),
        "compile_status": "UNKNOWN",
        "bytecode_status": "UNKNOWN",
        "node_count": 0,
        "bytecode_size": 0,
        "gas_estimate": 0,
        "errors": [],
        "duration_ms": 0,
    }

    start = time.perf_counter()

    # Phase 1: AST compilation
    try:
        ast = hlfc_compile(source)
        nodes = ast.get("program", [])
        report["compile_status"] = "OK"
        report["node_count"] = len(nodes)
        report["gas_estimate"] = len(nodes)  # 1 gas per node
    except Exception as exc:
        report["compile_status"] = "FAILED"
        report["errors"].append(f"Compile: {exc}")
        report["duration_ms"] = round((time.perf_counter() - start) * 1000, 1)
        return report

    # Phase 2: Bytecode compilation
    try:
        compiler = BytecodeCompiler()
        bytecode = compiler.compile(ast)
        report["bytecode_status"] = "OK"
        report["bytecode_size"] = len(bytecode)
    except Exception as exc:
        report["bytecode_status"] = "FAILED"
        report["errors"].append(f"Bytecode: {exc}")

    report["duration_ms"] = round((time.perf_counter() - start) * 1000, 1)
    return report


def generate_report(report: dict, output_dir: Path) -> Path:
    """Write a markdown report for a single program."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{report['name']}.md"

    compile_icon = "✅" if report["compile_status"] == "OK" else "❌"
    bytecode_icon = "✅" if report["bytecode_status"] == "OK" else "⚠️"

    lines = [
        f"# {report['name']}",
        "",
        f"**Source:** `{report['file']}` ({report['source_lines']} lines)",
        f"**Compiled:** {report['duration_ms']}ms",
        "",
        "| Phase | Status |",
        "|-------|--------|",
        f"| AST Compilation | {compile_icon} {report['compile_status']} |",
        f"| Bytecode Compilation | {bytecode_icon} {report['bytecode_status']} |",
        "",
        f"**AST Nodes:** {report['node_count']}",
        f"**Bytecode Size:** {report['bytecode_size']} bytes",
        f"**Gas Estimate:** {report['gas_estimate']} units",
    ]

    if report["errors"]:
        lines.extend(["", "## Errors", ""])
        for err in report["errors"]:
            lines.append(f"- {err}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def update_gallery_index(reports: list[dict], programs_dir: Path) -> None:
    """Update the hlf_programs/README.md gallery index."""
    readme = programs_dir / "README.md"

    ok_count = sum(1 for r in reports if r["compile_status"] == "OK")
    total = len(reports)

    lines = [
        "# HLF Program Gallery",
        "",
        f"> **{ok_count}/{total}** programs compile successfully",
        "",
        "| Program | Lines | AST | Bytecode | Gas | Time |",
        "|---------|-------|-----|----------|-----|------|",
    ]

    for r in reports:
        c = "✅" if r["compile_status"] == "OK" else "❌"
        b = "✅" if r["bytecode_status"] == "OK" else "⚠️"
        lines.append(
            f"| [{r['name']}](reports/{r['name']}.md) | {r['source_lines']} "
            f"| {c} | {b} | {r['gas_estimate']} | {r['duration_ms']}ms |"
        )

    lines.extend([
        "",
        "## Running",
        "",
        "```bash",
        "python scripts/run_hlf_gallery.py",
        "```",
        "",
        f"*Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}*",
    ])

    readme.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    programs_dir = _PROJECT_ROOT / "hlf_programs"

    if not programs_dir.exists():
        print(f"Program directory not found: {programs_dir}")
        return 1

    programs = discover_programs(programs_dir)
    if not programs:
        print("No .hlf programs found.")
        return 0

    print(f"Found {len(programs)} HLF programs\n")

    reports = []
    for prog in programs:
        report = compile_program(prog)
        reports.append(report)

        status = "✅" if report["compile_status"] == "OK" else "❌"
        bytecode = f" | bytecode: {report['bytecode_size']}B" if report["bytecode_status"] == "OK" else ""
        print(f"  {status} {report['name']:30s} ({report['source_lines']:3d} lines, {report['node_count']:3d} nodes{bytecode})")

    # Generate reports
    reports_dir = programs_dir / "reports"
    for report in reports:
        generate_report(report, reports_dir)

    # Update gallery index
    update_gallery_index(reports, programs_dir)

    ok = sum(1 for r in reports if r["compile_status"] == "OK")
    print(f"\n{ok}/{len(reports)} programs compiled successfully")
    print(f"Reports: {reports_dir}")
    print(f"Gallery: {programs_dir / 'README.md'}")

    return 0 if ok == len(reports) else 1


if __name__ == "__main__":
    sys.exit(main())
