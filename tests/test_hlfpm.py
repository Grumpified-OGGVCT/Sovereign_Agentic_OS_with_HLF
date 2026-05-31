"""
tests/test_hlfpm.py — Unit tests for the HLF Package Manager.

Tests install/uninstall/list/search/update/freeze operations,
lockfile persistence, and CLI. All OCI calls are mocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hlf.hlfpm import HLFPackageManager, _cli_main
from hlf.oci_client import OCIPullResult, OCIRegistryError

# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_client():
    """Create a mocked OCIClient."""
    client = MagicMock()
    return client


@pytest.fixture
def pm(tmp_path, mock_client):
    """Create a PackageManager with temp dirs and mocked OCI client."""
    return HLFPackageManager(
        modules_dir=tmp_path / "hlf_modules",
        oci_client=mock_client,
    )


def _make_pull_result(name="math", tag="v1.0.0", content=b"[HLF-v2] test", tmp_path=None):
    """Create a mock OCIPullResult."""
    path = tmp_path / f"{name}.hlf" if tmp_path else Path(f"/tmp/{name}.hlf")
    if tmp_path:
        path.write_bytes(content)
    return OCIPullResult(
        ref=f"ghcr.io/Grumpified-OGGVCT/hlf-modules/{name}:{tag}",
        local_path=path,
        sha256="abc123" * 10 + "abcd",
        size_bytes=len(content),
        cached=False,
        pull_time_ms=42.0,
    )


# ─── Install Tests ──────────────────────────────────────────────────────────


class TestInstall:

    def test_install_basic(self, pm, mock_client, tmp_path):
        pull_result = _make_pull_result(tmp_path=tmp_path)
        mock_client.pull.return_value = pull_result

        rec = pm.install("math@v1.0.0")
        assert rec.name == "math"
        assert rec.version == "v1.0.0"
        assert pm.is_installed("math")

    def test_install_creates_file(self, pm, mock_client, tmp_path):
        pull_result = _make_pull_result(tmp_path=tmp_path)
        mock_client.pull.return_value = pull_result

        pm.install("math@v1.0.0")
        assert (pm.modules_dir / "math.hlf").exists()

    def test_install_already_installed_skips(self, pm, mock_client, tmp_path):
        pull_result = _make_pull_result(tmp_path=tmp_path)
        mock_client.pull.return_value = pull_result

        pm.install("math@v1.0.0")
        pm.install("math@v1.0.0")  # should skip
        assert mock_client.pull.call_count == 1  # only called once

    def test_install_force_repulls(self, pm, mock_client, tmp_path):
        pull_result = _make_pull_result(tmp_path=tmp_path)
        mock_client.pull.return_value = pull_result

        pm.install("math@v1.0.0")
        pm.install("math@v1.0.0", force=True)
        assert mock_client.pull.call_count == 2

    def test_install_bare_name(self, pm, mock_client, tmp_path):
        pull_result = _make_pull_result(tmp_path=tmp_path)
        mock_client.pull.return_value = pull_result

        rec = pm.install("math")
        assert rec.name == "math"
        assert rec.version == "latest"


# ─── Uninstall Tests ─────────────────────────────────────────────────────────


class TestUninstall:

    def test_uninstall_existing(self, pm, mock_client, tmp_path):
        pull_result = _make_pull_result(tmp_path=tmp_path)
        mock_client.pull.return_value = pull_result

        pm.install("math@v1.0.0")
        assert pm.uninstall("math") is True
        assert not pm.is_installed("math")

    def test_uninstall_nonexistent(self, pm):
        assert pm.uninstall("nonexistent") is False

    def test_uninstall_removes_file(self, pm, mock_client, tmp_path):
        pull_result = _make_pull_result(tmp_path=tmp_path)
        mock_client.pull.return_value = pull_result

        pm.install("math@v1.0.0")
        pm.uninstall("math")
        assert not (pm.modules_dir / "math.hlf").exists()


# ─── List Tests ──────────────────────────────────────────────────────────────


class TestList:

    def test_list_empty(self, pm):
        assert pm.list_installed() == []

    def test_list_returns_sorted(self, pm, mock_client, tmp_path):
        for name in ["crypto", "math", "io"]:
            result = _make_pull_result(name=name, tmp_path=tmp_path)
            mock_client.pull.return_value = result
            pm.install(name)

        modules = pm.list_installed()
        names = [m.name for m in modules]
        assert names == ["crypto", "io", "math"]


# ─── Search Tests ────────────────────────────────────────────────────────────


class TestSearch:

    def test_search_returns_tags(self, pm, mock_client):
        mock_client.list_tags.return_value = ["v1.0.0", "v2.0.0", "latest"]
        tags = pm.search("math")
        assert "v1.0.0" in tags

    def test_search_error_returns_empty(self, pm, mock_client):
        mock_client.list_tags.side_effect = OCIRegistryError("not found")
        tags = pm.search("unknown")
        assert tags == []


# ─── Update Tests ────────────────────────────────────────────────────────────


class TestUpdate:

    def test_update_installed(self, pm, mock_client, tmp_path):
        result = _make_pull_result(tmp_path=tmp_path)
        mock_client.pull.return_value = result
        pm.install("math@v1.0.0")

        result2 = _make_pull_result(tag="latest", tmp_path=tmp_path)
        mock_client.pull.return_value = result2
        rec = pm.update("math")
        assert rec is not None
        assert rec.version == "latest"

    def test_update_not_installed(self, pm):
        assert pm.update("unknown") is None


# ─── Freeze Tests ────────────────────────────────────────────────────────────


class TestFreeze:

    def test_freeze_empty(self, pm):
        assert pm.freeze() == {}

    def test_freeze_contains_versions(self, pm, mock_client, tmp_path):
        result = _make_pull_result(tmp_path=tmp_path)
        mock_client.pull.return_value = result
        pm.install("math@v1.0.0")

        frozen = pm.freeze()
        assert "math" in frozen
        assert frozen["math"]["version"] == "v1.0.0"
        assert "sha256" in frozen["math"]


# ─── Lockfile Tests ──────────────────────────────────────────────────────────


class TestLockfile:

    def test_lockfile_created_on_install(self, pm, mock_client, tmp_path):
        result = _make_pull_result(tmp_path=tmp_path)
        mock_client.pull.return_value = result
        pm.install("math@v1.0.0")

        assert pm._lockfile_path.exists()
        data = json.loads(pm._lockfile_path.read_text())
        assert "math" in data["modules"]

    def test_lockfile_roundtrip(self, tmp_path, mock_client):
        """Install, create new manager, verify state persists."""
        result = _make_pull_result(tmp_path=tmp_path)
        mock_client.pull.return_value = result

        pm1 = HLFPackageManager(
            modules_dir=tmp_path / "hlf_modules",
            oci_client=mock_client,
        )
        pm1.install("math@v1.0.0")

        # New manager reads lockfile
        pm2 = HLFPackageManager(
            modules_dir=tmp_path / "hlf_modules",
            oci_client=mock_client,
        )
        assert pm2.is_installed("math")

    def test_lockfile_empty_on_uninstall(self, pm, mock_client, tmp_path):
        result = _make_pull_result(tmp_path=tmp_path)
        mock_client.pull.return_value = result
        pm.install("math@v1.0.0")
        pm.uninstall("math")

        data = json.loads(pm._lockfile_path.read_text())
        assert len(data["modules"]) == 0


# ─── Module Path Tests ──────────────────────────────────────────────────────


class TestModulePath:

    def test_get_module_path_installed(self, pm, mock_client, tmp_path):
        result = _make_pull_result(tmp_path=tmp_path)
        mock_client.pull.return_value = result
        pm.install("math@v1.0.0")

        path = pm.get_module_path("math")
        assert path is not None
        assert "math.hlf" in str(path)

    def test_get_module_path_not_installed(self, pm):
        assert pm.get_module_path("unknown") is None


# ─── CLI Tests ───────────────────────────────────────────────────────────────


class TestCLI:

    def test_cli_no_args_shows_help(self, capsys):
        code = _cli_main([])
        assert code == 0

    def test_cli_unknown_command(self, capsys):
        code = _cli_main(["bogus"])
        assert code == 1

    def test_cli_list_empty(self, capsys):
        with patch("hlf.hlfpm.HLFPackageManager") as MockPM:
            MockPM.return_value.list_installed.return_value = []
            code = _cli_main(["list"])
            assert code == 0

    def test_cli_freeze(self, capsys):
        with patch("hlf.hlfpm.HLFPackageManager") as MockPM:
            MockPM.return_value.freeze.return_value = {"math": {"version": "v1"}}
            code = _cli_main(["freeze"])
            assert code == 0
