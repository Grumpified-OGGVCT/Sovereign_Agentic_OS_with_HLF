"""
HLF Tool Scaffold — Generate new tool project templates.

Provides `hlf new-tool <name>` to scaffold a ready-to-publish tool repo
with:
  - tool.hlf.yaml manifest (pre-filled)
  - main.py entrypoint (with ToolAdapter pattern)
  - tests/test_tool.py (starter test)
  - README.md (auto-generated from manifest)
  - .gitignore

Usage::

    from hlf.tool_scaffold import scaffold_tool

    scaffold_tool("my_agent", output_dir="./tools")
    # Creates ./tools/my_agent/ with full structure

CLI::

    hlf new-tool my_agent --adapter python --tier hearth
"""

from __future__ import annotations

import textwrap
from pathlib import Path


def scaffold_tool(
    name: str,
    output_dir: Path | str = ".",
    description: str = "",
    author: str = "",
    adapter: str = "python",
    tier: str = "hearth",
) -> Path:
    """Generate a new tool project from template.

    Args:
        name: Tool name (lowercase, alphanumeric + underscore)
        output_dir: Where to create the tool directory
        description: Human-readable description
        author: Author name or GitHub handle
        adapter: Runtime adapter (python, docker, wasm, mcp)
        tier: Default deployment tier

    Returns:
        Path to the created tool directory
    """
    root = Path(output_dir) / name
    root.mkdir(parents=True, exist_ok=True)

    desc = description or f"A {adapter}-based tool for the Sovereign OS"

    # 1. tool.hlf.yaml manifest
    (root / "tool.hlf.yaml").write_text(textwrap.dedent(f"""\
        # Tool Manifest for Sovereign OS
        # See: hlf/tool_installer.py for schema documentation

        name: "{name}"
        version: "0.1.0"
        description: "{desc}"
        author: "{author}"
        license: "MIT"

        # Deployment
        tier: ["{tier}"]
        gas_cost: 1
        sensitive: false

        # Entry point
        entrypoint: "main.py"
        function: "run"
        adapter: "{adapter}"

        # Dependencies (installed in isolated venv)
        dependencies:
          python: ">=3.11"
          packages: []

        # Permissions (zero-trust)
        permissions:
          network: []
          filesystem: ["./data"]
          secrets: []

        # Health check
        health:
          endpoint: "health_check"
          interval_seconds: 300

        # Arguments (JSON Schema)
        args:
          - name: "input"
            type: "string"
            required: true

        # Signature (fill before publishing)
        signature:
          sha256: ""
          signed_by: "{author}"
    """), encoding="utf-8")

    # 2. main.py — entrypoint with ToolAdapter pattern
    (root / "main.py").write_text(textwrap.dedent(f"""\
        \\"\\"\\"
        {name} — tool for the Sovereign Agentic OS.

        {desc}

        Implements the ToolAdapter pattern for integration with
        hlf install / τ({name.upper()}) dispatch.
        \\"\\"\\"

        from __future__ import annotations

        from dataclasses import dataclass
        from typing import Any


        @dataclass
        class ToolResult:
            \\"\\"\\"Result from tool execution.\\"\\"\\"
            success: bool
            value: Any = None
            error: str | None = None


        # ─── Core Functions ──────────────────────────────────────────────────


        def run(input: str, **kwargs: Any) -> ToolResult:
            \\"\\"\\"Main entry point — called by Sovereign OS via τ({name.upper()}).

            Args:
                input: The primary input argument

            Returns:
                ToolResult with success status and output value
            \\"\\"\\"
            try:
                result = _process(input, **kwargs)
                return ToolResult(success=True, value=result)
            except Exception as e:
                return ToolResult(success=False, error=str(e))


        def health_check() -> bool:
            \\"\\"\\"Health check endpoint — called by hlf health {name}.\\"\\"\\"
            return True


        def schema() -> dict:
            \\"\\"\\"Return JSON Schema for accepted arguments.\\"\\"\\"
            return {{
                "type": "object",
                "properties": {{
                    "input": {{
                        "type": "string",
                        "description": "Primary input",
                    }},
                }},
                "required": ["input"],
            }}


        # ─── Internal Logic ──────────────────────────────────────────────────


        def _process(input: str, **kwargs: Any) -> str:
            \\"\\"\\"Core processing logic. Customize this.\\"\\"\\"
            return f"Processed: {{input}}"


        # ─── CLI ─────────────────────────────────────────────────────────────

        if __name__ == "__main__":
            import sys
            if len(sys.argv) > 1:
                result = run(sys.argv[1])
                print(result)
            else:
                print(f"Usage: python main.py <input>")
    """), encoding="utf-8")

    # 3. tests/test_tool.py
    tests_dir = root / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test_tool.py").write_text(textwrap.dedent(f"""\
        \\"\\"\\"Tests for {name} tool.\\"\\"\\"

        import sys
        from pathlib import Path

        # Add parent to path for import
        sys.path.insert(0, str(Path(__file__).parent.parent))

        from main import run, health_check, schema


        def test_run_basic():
            result = run("hello world")
            assert result.success
            assert "Processed" in str(result.value)


        def test_run_empty_input():
            result = run("")
            assert result.success


        def test_health_check():
            assert health_check() is True


        def test_schema_valid():
            s = schema()
            assert "properties" in s
            assert "input" in s["properties"]
    """), encoding="utf-8")

    # 4. README.md
    (root / "README.md").write_text(textwrap.dedent(f"""\
        # {name}

        {desc}

        ## Installation

        ```bash
        hlf install ./{name}
        # or from GitHub:
        # hlf install https://github.com/youruser/{name}
        ```

        ## Usage

        In HLF:
        ```
        ↦ τ({name.upper()}) input="your input here"
        ```

        In Python:
        ```python
        from main import run
        result = run("your input")
        print(result.value)
        ```

        ## Development

        ```bash
        cd {name}
        python -m pytest tests/
        ```

        ## License

        MIT
    """), encoding="utf-8")

    # 5. .gitignore
    (root / ".gitignore").write_text(textwrap.dedent("""\
        __pycache__/
        *.pyc
        .venv/
        .sandbox.json
        *.egg-info/
        dist/
        build/
        .pytest_cache/
    """), encoding="utf-8")

    # 6. data/ directory (for sandboxed filesystem)
    (root / "data").mkdir(exist_ok=True)
    (root / "data" / ".gitkeep").write_text("", encoding="utf-8")

    return root


# ─── CLI Entry ───────────────────────────────────────────────────────────────


def cli_new_tool(args: list[str] | None = None) -> int:
    """CLI: hlf new-tool <name> [--adapter python] [--tier hearth]."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="hlf new-tool",
        description="Scaffold a new Sovereign OS tool project",
    )
    parser.add_argument("name", help="Tool name (lowercase, a-z, 0-9, underscore)")
    parser.add_argument("--description", "-d", default="", help="Tool description")
    parser.add_argument("--author", "-a", default="", help="Author name")
    parser.add_argument("--adapter", default="python", choices=["python", "docker", "wasm", "mcp"])
    parser.add_argument("--tier", default="hearth", choices=["hearth", "forge", "sovereign"])
    parser.add_argument("--output", "-o", default=".", help="Output directory")

    parsed = parser.parse_args(args)

    path = scaffold_tool(
        name=parsed.name,
        output_dir=parsed.output,
        description=parsed.description,
        author=parsed.author,
        adapter=parsed.adapter,
        tier=parsed.tier,
    )

    print(f"✅ Tool scaffolded at: {path}")
    print(f"   Next steps:")
    print(f"   1. cd {parsed.name}")
    print(f"   2. Edit main.py with your logic")
    print(f"   3. python -m pytest tests/")
    print(f"   4. hlf install ./{parsed.name}")
    return 0
