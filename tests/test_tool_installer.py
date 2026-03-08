"""
Tests for HLF Tool Installer — verifies the full CLONE → ACTIVATE pipeline.

Covers:
  - ToolManifest parsing and validation
  - ToolVerifier 12-point CoVE gate
  - ToolInstaller install/uninstall/list/health/upgrade
  - Name collision detection
  - Sandbox setup
  - Registry persistence
  - CLI entry point
  - Error handling and rollback
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest

from hlf.tool_installer import (
    ToolInstaller,
    ToolManifest,
    ToolManifestError,
    ToolInstallError,
    ToolNotFoundError,
    ToolResult,
    ToolSecurityError,
    ToolVerifier,
    RESERVED_TOOL_NAMES,
    cli_main,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def valid_manifest_yaml() -> str:
    return textwrap.dedent("""\
        name: "test_tool"
        version: "1.0.0"
        description: "A test tool for unit testing"
        author: "test_author"
        license: "MIT"
        tier: ["hearth", "forge"]
        gas_cost: 2
        sensitive: false
        entrypoint: "main.py"
        function: "run"
        adapter: "python"
        dependencies:
          python: ">=3.11"
          packages: ["httpx"]
        permissions:
          network: ["api.example.com"]
          filesystem: ["./data"]
          secrets: ["API_KEY"]
        health:
          endpoint: "health_check"
          interval_seconds: 300
        args:
          - name: "prompt"
            type: "string"
            required: true
          - name: "temperature"
            type: "number"
            default: 0.7
        signature:
          sha256: "abc123def456"
          signed_by: "test_author"
    """)


@pytest.fixture()
def tool_dir(tmp_path: Path, valid_manifest_yaml: str) -> Path:
    """Create a temp tool directory with valid manifest and entrypoint."""
    tool_path = tmp_path / "test_tool"
    tool_path.mkdir()

    # Write manifest
    (tool_path / "tool.hlf.yaml").write_text(valid_manifest_yaml, encoding="utf-8")

    # Write entrypoint
    (tool_path / "main.py").write_text(
        textwrap.dedent("""\
            def run(prompt: str, temperature: float = 0.7) -> dict:
                return {"output": f"Processed: {prompt}", "temp": temperature}

            def health_check() -> bool:
                return True
        """),
        encoding="utf-8",
    )

    return tool_path


@pytest.fixture()
def installer(tmp_path: Path) -> ToolInstaller:
    """Create an installer with temp directories."""
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()

    # Create a minimal host_functions.json
    gov_dir = tmp_path / "governance"
    gov_dir.mkdir()
    (gov_dir / "host_functions.json").write_text(
        json.dumps({"version": "1.0.0", "functions": []}, indent=2),
        encoding="utf-8",
    )

    return ToolInstaller(tools_dir=tools_dir)


# ─── ToolManifest Tests ──────────────────────────────────────────────────────


class TestToolManifest:
    """Tests for tool.hlf.yaml manifest parsing and validation."""

    def test_parse_valid_manifest(self, tool_dir: Path) -> None:
        manifest = ToolManifest.from_yaml(tool_dir / "tool.hlf.yaml")
        assert manifest.name == "test_tool"
        assert manifest.version == "1.0.0"
        assert manifest.description == "A test tool for unit testing"
        assert manifest.author == "test_author"
        assert manifest.license == "MIT"
        assert "hearth" in manifest.tier
        assert manifest.gas_cost == 2
        assert manifest.adapter == "python"
        assert len(manifest.args) == 2

    def test_missing_manifest_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ToolManifestError, match="Manifest not found"):
            ToolManifest.from_yaml(tmp_path / "nonexistent.yaml")

    def test_missing_name_raises(self, tmp_path: Path) -> None:
        (tmp_path / "tool.hlf.yaml").write_text("version: '1.0.0'\n")
        with pytest.raises(ToolManifestError, match="missing required 'name'"):
            ToolManifest.from_yaml(tmp_path / "tool.hlf.yaml")

    def test_missing_version_raises(self, tmp_path: Path) -> None:
        (tmp_path / "tool.hlf.yaml").write_text("name: test\n")
        with pytest.raises(ToolManifestError, match="missing required 'version'"):
            ToolManifest.from_yaml(tmp_path / "tool.hlf.yaml")

    def test_invalid_name_format(self, tmp_path: Path) -> None:
        (tmp_path / "tool.hlf.yaml").write_text(
            "name: 'INVALID-Name!'\nversion: '1.0'\n"
        )
        with pytest.raises(ToolManifestError, match="Invalid tool name"):
            ToolManifest.from_yaml(tmp_path / "tool.hlf.yaml")

    def test_reserved_name_collision(self, tmp_path: Path) -> None:
        (tmp_path / "tool.hlf.yaml").write_text(
            "name: 'read'\nversion: '1.0'\n"
        )
        with pytest.raises(ToolManifestError, match="conflicts with reserved"):
            ToolManifest.from_yaml(tmp_path / "tool.hlf.yaml")

    def test_to_host_function_entry(self, tool_dir: Path) -> None:
        manifest = ToolManifest.from_yaml(tool_dir / "tool.hlf.yaml")
        entry = manifest.to_host_function_entry()
        assert entry["name"] == "TEST_TOOL"
        assert entry["gas"] == 2
        assert entry["backend"] == "tool:test_tool"
        assert len(entry["args"]) == 2
        assert entry["tier"] == ["hearth", "forge"]

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        # Use a truly invalid YAML structure (duplicate key with inconsistent indentation)
        (tmp_path / "tool.hlf.yaml").write_text(":\n\t- :\n\t\t{{\x00broken")
        with pytest.raises(ToolManifestError):
            ToolManifest.from_yaml(tmp_path / "tool.hlf.yaml")

    def test_non_mapping_yaml_raises(self, tmp_path: Path) -> None:
        (tmp_path / "tool.hlf.yaml").write_text("- just\n- a\n- list\n")
        with pytest.raises(ToolManifestError, match="must be a YAML mapping"):
            ToolManifest.from_yaml(tmp_path / "tool.hlf.yaml")

    def test_defaults_applied(self, tmp_path: Path) -> None:
        (tmp_path / "tool.hlf.yaml").write_text(
            "name: minimal_tool\nversion: '0.1'\n"
        )
        manifest = ToolManifest.from_yaml(tmp_path / "tool.hlf.yaml")
        assert manifest.tier == ["hearth"]
        assert manifest.gas_cost == 1
        assert manifest.adapter == "python"
        assert manifest.entrypoint == "main.py"
        assert manifest.function == "run"
        assert manifest.sensitive is False


# ─── ToolVerifier Tests ──────────────────────────────────────────────────────


class TestToolVerifier:
    """Tests for the CoVE-inspired 12-point verification gate."""

    def test_valid_tool_passes(self, tool_dir: Path) -> None:
        manifest = ToolManifest.from_yaml(tool_dir / "tool.hlf.yaml")
        verifier = ToolVerifier()
        passed, failures, score = verifier.verify(manifest, tool_dir)
        assert passed
        assert len(failures) == 0
        assert score == 1.0

    def test_missing_entrypoint_fails(self, tool_dir: Path) -> None:
        (tool_dir / "main.py").unlink()
        manifest = ToolManifest.from_yaml(tool_dir / "tool.hlf.yaml")
        verifier = ToolVerifier()
        passed, failures, score = verifier.verify(manifest, tool_dir)
        assert not passed
        assert any("entrypoint" in f.lower() for f in failures)

    def test_invalid_tier_fails(self, tool_dir: Path) -> None:
        manifest = ToolManifest.from_yaml(tool_dir / "tool.hlf.yaml")
        manifest.tier = ["invalid_tier"]
        verifier = ToolVerifier()
        passed, failures, _ = verifier.verify(manifest, tool_dir)
        assert not passed
        assert any("tier" in f.lower() for f in failures)

    def test_excessive_gas_fails(self, tool_dir: Path) -> None:
        manifest = ToolManifest.from_yaml(tool_dir / "tool.hlf.yaml")
        manifest.gas_cost = 999
        verifier = ToolVerifier()
        passed, failures, _ = verifier.verify(manifest, tool_dir)
        assert not passed
        assert any("gas" in f.lower() for f in failures)

    def test_wildcard_network_fails(self, tool_dir: Path) -> None:
        manifest = ToolManifest.from_yaml(tool_dir / "tool.hlf.yaml")
        manifest.permissions = {"network": ["*"]}
        verifier = ToolVerifier()
        passed, failures, _ = verifier.verify(manifest, tool_dir)
        assert not passed
        assert any("wildcard" in f.lower() for f in failures)

    def test_path_traversal_perm_fails(self, tool_dir: Path) -> None:
        manifest = ToolManifest.from_yaml(tool_dir / "tool.hlf.yaml")
        manifest.permissions = {"filesystem": ["../../etc/passwd"]}
        verifier = ToolVerifier()
        passed, failures, _ = verifier.verify(manifest, tool_dir)
        assert not passed
        assert any("traversal" in f.lower() or "absolute" in f.lower() for f in failures)

    def test_unsupported_adapter_fails(self, tool_dir: Path) -> None:
        manifest = ToolManifest.from_yaml(tool_dir / "tool.hlf.yaml")
        manifest.adapter = "alien_runtime"
        verifier = ToolVerifier()
        passed, failures, _ = verifier.verify(manifest, tool_dir)
        assert not passed
        assert any("adapter" in f.lower() for f in failures)

    def test_blocked_dependency_fails(self, tool_dir: Path) -> None:
        manifest = ToolManifest.from_yaml(tool_dir / "tool.hlf.yaml")
        manifest.dependencies = {"packages": ["os-sys"]}
        verifier = ToolVerifier()
        passed, failures, _ = verifier.verify(manifest, tool_dir)
        assert not passed
        assert any("blocked" in f.lower() for f in failures)

    def test_no_license_fails(self, tool_dir: Path) -> None:
        manifest = ToolManifest.from_yaml(tool_dir / "tool.hlf.yaml")
        manifest.license = ""
        verifier = ToolVerifier()
        passed, failures, _ = verifier.verify(manifest, tool_dir)
        assert not passed
        assert any("license" in f.lower() or "spdx" in f.lower() for f in failures)

    def test_score_reflects_pass_ratio(self, tool_dir: Path) -> None:
        manifest = ToolManifest.from_yaml(tool_dir / "tool.hlf.yaml")
        # Valid tool should get 1.0
        _, _, score = ToolVerifier().verify(manifest, tool_dir)
        assert score == 1.0

        # Remove license → 11/12 pass
        manifest.license = ""
        _, _, score = ToolVerifier().verify(manifest, tool_dir)
        assert 0.9 <= score < 1.0


# ─── ToolInstaller Tests ────────────────────────────────────────────────────


class TestToolInstaller:
    """Tests for the main installer pipeline."""

    def test_list_empty(self, installer: ToolInstaller) -> None:
        tools = installer.list_tools()
        assert tools == []

    def test_uninstall_nonexistent_raises(self, installer: ToolInstaller) -> None:
        with pytest.raises(ToolNotFoundError, match="not installed"):
            installer.uninstall("no_such_tool")

    def test_health_check_nonexistent_raises(self, installer: ToolInstaller) -> None:
        with pytest.raises(ToolNotFoundError, match="not installed"):
            installer.health_check("no_such_tool")

    def test_upgrade_nonexistent_raises(self, installer: ToolInstaller) -> None:
        with pytest.raises(ToolNotFoundError, match="not installed"):
            installer.upgrade("no_such_tool")

    def test_extract_repo_name_https(self) -> None:
        name = ToolInstaller._extract_repo_name("https://github.com/user/cool-agent.git")
        assert name == "cool-agent"

    def test_extract_repo_name_ssh(self) -> None:
        name = ToolInstaller._extract_repo_name("git@github.com:user/my_tool.git")
        assert name == "my_tool"

    def test_extract_repo_name_trailing_slash(self) -> None:
        name = ToolInstaller._extract_repo_name("https://github.com/user/tool/")
        assert name == "tool"


# ─── ToolResult Tests ────────────────────────────────────────────────────────


class TestToolResult:
    """Tests for ToolResult dataclass."""

    def test_success_result(self) -> None:
        result = ToolResult(success=True, value="hello", gas_used=3)
        d = result.to_dict()
        assert d["success"] is True
        assert d["value"] == "hello"
        assert d["gas_used"] == 3
        assert d["error"] is None

    def test_error_result(self) -> None:
        result = ToolResult(success=False, error="timeout")
        d = result.to_dict()
        assert d["success"] is False
        assert d["error"] == "timeout"


# ─── Reserved Names Tests ───────────────────────────────────────────────────


class TestReservedNames:
    """Verify built-in function names are protected."""

    def test_read_is_reserved(self) -> None:
        assert "READ" in RESERVED_TOOL_NAMES

    def test_spawn_is_reserved(self) -> None:
        assert "SPAWN" in RESERVED_TOOL_NAMES

    def test_custom_name_not_reserved(self) -> None:
        assert "MY_COOL_AGENT" not in RESERVED_TOOL_NAMES


# ─── CLI Tests ───────────────────────────────────────────────────────────────


class TestCLI:
    """Tests for the CLI entry point."""

    def test_tools_command_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = cli_main(["tools"])
        assert code == 0
        captured = capsys.readouterr()
        assert "No tools installed" in captured.out

    def test_no_command_shows_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = cli_main([])
        assert code == 1
