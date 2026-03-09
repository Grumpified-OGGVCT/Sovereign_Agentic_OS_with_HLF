"""
HLF Interactive REPL — Read-Eval-Print Loop for the Hieroglyphic Logic Framework.

Usage:
  python -m hlf.hlfsh          # Launch interactive shell
  echo '[SET] x = 1' | python -m hlf.hlfsh   # Pipe mode

Built-in commands:
  :help    — Show available commands
  :env     — Display current variable bindings
  :gas     — Show gas meter status
  :reset   — Clear environment and gas meter
  :load    — Load and execute a .hlf file
  :ast     — Show AST of last evaluated statement
  :lint    — Lint last input
  :quit    — Exit the REPL

Features:
  - Persistent session environment (SET bindings accumulate)
  - Per-line gas metering with cumulative display
  - History persistence via readline (~/.hlf_history)
  - ANSI color output for better readability
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# ─── ANSI Color Support ─────────────────────────────────────────────────────

_COLORS_ENABLED = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    """Wrap text in ANSI escape code if colors are enabled."""
    if _COLORS_ENABLED:
        return f"\033[{code}m{text}\033[0m"
    return text


def _green(text: str) -> str:
    return _c(text, "32")


def _yellow(text: str) -> str:
    return _c(text, "33")


def _red(text: str) -> str:
    return _c(text, "31")


def _cyan(text: str) -> str:
    return _c(text, "36")


def _dim(text: str) -> str:
    return _c(text, "2")


def _bold(text: str) -> str:
    return _c(text, "1")


# ─── REPL Commands ──────────────────────────────────────────────────────────

HELP_TEXT = """
HLF Interactive Shell (hlfsh) — Commands:
  :help    Show this help message
  :env     Display current variable bindings
  :gas     Show gas meter status
  :reset   Clear environment and gas meter
  :load F  Load and execute a .hlf file
  :ast     Show AST of last evaluated statement
  :lint    Lint last input via hlflint
  :quit    Exit the REPL (also: Ctrl+D)
""".strip()


# ─── REPL Session ───────────────────────────────────────────────────────────


class HLFShell:
    """Interactive HLF shell session.

    Maintains a persistent environment across evaluations.
    Gas meter accumulates across statements within a session.
    """

    PROMPT = "hlf> "
    CONTINUATION_PROMPT = "...> "

    def __init__(self, gas_limit: int = 1000) -> None:
        self.env: dict[str, Any] = {}
        self.gas_limit = gas_limit
        self.gas_used = 0
        self.last_ast: dict[str, Any] | None = None
        self.last_input: str = ""
        self.history_path = Path.home() / ".hlf_history"
        self.statement_count = 0

    def _try_compile(self, source: str) -> dict[str, Any] | None:
        """Attempt to compile HLF source. Returns AST dict or None."""
        try:
            from hlf.hlfc import compile as hlfc_compile
            return hlfc_compile(source)
        except Exception:
            return None

    def _count_ast_nodes(self, ast: dict[str, Any]) -> int:
        """Count the number of AST nodes for gas accounting."""
        program = ast.get("program", [])
        return len(program)

    def eval(self, source: str) -> str:
        """Evaluate HLF source and return formatted output string.

        This is the core evaluation method, suitable for programmatic use.
        Does NOT require an interactive terminal.
        """
        self.last_input = source.strip()

        if not self.last_input:
            return ""

        # Try to compile
        ast = self._try_compile(self.last_input)
        if ast is None:
            return _red("✗ Compile error — check syntax")

        self.last_ast = ast

        # Gas accounting — count AST nodes
        node_count = self._count_ast_nodes(ast)
        self.gas_used += node_count
        self.statement_count += 1

        # Extract results from AST
        lines: list[str] = []
        program = ast.get("program", [])

        # Collect SET bindings
        new_bindings: dict[str, str] = {}
        for node in program:
            if not node:
                continue
            tag = node.get("tag", "")
            if tag == "SET":
                name = node.get("name", "")
                value = node.get("value", "")
                if name:
                    new_bindings[name] = value
                    self.env[name] = value

        # Show SET bindings
        for name, value in new_bindings.items():
            lines.append(_green(f"  ⟐ {name} = {value}"))

        # Show final RESULT if present
        for node in program:
            if not node:
                continue
            if node.get("tag") == "RESULT":
                code = node.get("code", 0)
                message = node.get("message", "")
                status = _green("✓") if code == 0 else _red("✗")
                lines.append(f"{status} [{code}] {message}")

        # Gas summary
        remaining = self.gas_limit - self.gas_used
        lines.append(_dim(f"  ⩕ gas: {node_count} nodes ({self.gas_used}/{self.gas_limit} total, {remaining} remaining)"))

        if self.gas_used >= self.gas_limit:
            lines.append(_red("  ⚠ GAS LIMIT REACHED — use :reset or increase --gas-limit"))

        return "\n".join(lines)

    def handle_command(self, command: str) -> str | None:
        """Handle a :command. Returns output string or None if not a command."""
        cmd = command.strip()

        if not cmd.startswith(":"):
            return None

        parts = cmd.split(maxsplit=1)
        action = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if action == ":help":
            return HELP_TEXT

        if action == ":env":
            if not self.env:
                return _dim("(empty environment)")
            lines = []
            for k, v in sorted(self.env.items()):
                if not str(k).startswith("__"):
                    lines.append(f"  {_green(str(k))} = {v}")
            return "\n".join(lines) or _dim("(empty environment)")

        if action == ":gas":
            remaining = self.gas_limit - self.gas_used
            pct = (self.gas_used / self.gas_limit * 100) if self.gas_limit else 0
            color = _green if pct < 50 else (_yellow if pct < 80 else _red)
            return (
                f"  Gas used:      {color(str(self.gas_used))}\n"
                f"  Gas remaining: {remaining}\n"
                f"  Gas limit:     {self.gas_limit}\n"
                f"  Utilization:   {color(f'{pct:.1f}%')}\n"
                f"  Statements:    {self.statement_count}"
            )

        if action == ":reset":
            self.env.clear()
            self.gas_used = 0
            self.last_ast = None
            self.last_input = ""
            self.statement_count = 0
            return _green("✓ Session reset — environment and gas cleared")

        if action == ":load":
            if not arg:
                return _red("Usage: :load <filepath.hlf>")
            path = Path(arg)
            if not path.exists():
                return _red(f"✗ File not found: {path}")
            try:
                source = path.read_text(encoding="utf-8")
                return self.eval(source)
            except Exception as exc:
                return _red(f"✗ Load error: {exc}")

        if action == ":ast":
            if self.last_ast is None:
                return _dim("(no AST — evaluate something first)")
            return json.dumps(self.last_ast, indent=2, ensure_ascii=False)

        if action == ":lint":
            if not self.last_input:
                return _dim("(no input to lint)")
            try:
                from hlf.hlflint import lint
                issues = lint(self.last_input)
                if not issues:
                    return _green("✓ No lint issues")
                return "\n".join(_yellow(f"  ⚠ {issue}") for issue in issues)
            except Exception as exc:
                return _red(f"✗ Lint error: {exc}")

        if action in (":quit", ":exit", ":q"):
            raise SystemExit(0)

        return _red(f"Unknown command: {action}. Type :help for available commands.")

    def _setup_readline(self) -> None:
        """Configure readline with history support."""
        try:
            import readline

            if self.history_path.exists():
                readline.read_history_file(str(self.history_path))
            readline.set_history_length(1000)
        except (ImportError, OSError):
            pass  # readline not available on all platforms

    def _save_history(self) -> None:
        """Persist readline history to disk."""
        try:
            import readline
            readline.write_history_file(str(self.history_path))
        except (ImportError, OSError):
            pass

    def run(self) -> None:
        """Launch the interactive REPL loop."""
        self._setup_readline()

        print(_bold("HLF Interactive Shell") + _dim(f" (v0.4.0 • gas limit: {self.gas_limit})"))
        print(_dim("Type :help for commands, :quit to exit\n"))

        try:
            while True:
                try:
                    line = input(self.PROMPT)
                except EOFError:
                    print()
                    break

                # Handle commands
                cmd_result = self.handle_command(line)
                if cmd_result is not None:
                    print(cmd_result)
                    continue

                # Evaluate HLF
                output = self.eval(line)
                if output:
                    print(output)

        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            self._save_history()
            print(_dim("\nGoodbye."))


# ─── Entry Point ─────────────────────────────────────────────────────────────


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="HLF Interactive Shell")
    parser.add_argument("--gas-limit", type=int, default=1000, help="Gas budget (default: 1000)")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    args = parser.parse_args()

    if args.no_color:
        global _COLORS_ENABLED
        _COLORS_ENABLED = False

    shell = HLFShell(gas_limit=args.gas_limit)
    shell.run()


if __name__ == "__main__":
    main()
