"""
Tests for AppInstaller — universal GitHub-to-OS app pipeline.

Tests cover:
  - Release detection (mocked GitHub API)
  - Install strategy inference
  - App manifest serialization
  - Install pipeline (mocked git/pip)
  - Uninstall
  - Update checks
  - Registry persistence
  - Listing and stats
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from agents.core.app_installer import (
    AppInstaller,
    AppManifest,
    AppStatus,
    AppCategory,
    EnvType,
    ReleaseInfo,
    detect_install_strategy,
    fetch_latest_release,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def apps_dir(tmp_path: Path) -> Path:
    d = tmp_path / "apps"
    d.mkdir()
    return d


@pytest.fixture
def installer(apps_dir: Path) -> AppInstaller:
    return AppInstaller(apps_dir=apps_dir)


@pytest.fixture
def mock_release() -> ReleaseInfo:
    return ReleaseInfo(
        tag="v1.0.0",
        name="Release 1.0.0",
        published_at="2026-01-01T00:00:00Z",
        assets=[{"name": "app.zip", "url": "https://example.com/app.zip", "size": 1024, "content_type": "application/zip"}],
        body="First release",
    )


# ─── Manifest ────────────────────────────────────────────────────────────────

class TestManifest:
    def test_to_dict(self) -> None:
        m = AppManifest(name="test", repo="owner/test", version="1.0")
        d = m.to_dict()
        assert d["name"] == "test"
        assert d["repo"] == "owner/test"

    def test_from_dict(self) -> None:
        data = {"name": "test", "repo": "owner/test", "version": "1.0", "category": "ai_tool", "status": "installed", "env_type": "venv"}
        m = AppManifest.from_dict(data)
        assert m.name == "test"
        assert m.category == AppCategory.AI_TOOL

    def test_roundtrip(self) -> None:
        m = AppManifest(name="app", repo="org/app", version="2.0", category=AppCategory.AGENT)
        m2 = AppManifest.from_dict(m.to_dict())
        assert m.name == m2.name
        assert m.repo == m2.repo
        assert m.category == m2.category


# ─── Install Strategy ───────────────────────────────────────────────────────

class TestInstallStrategy:
    def test_python_repo(self) -> None:
        info = {"language": "Python", "topics": []}
        strategy = detect_install_strategy(info, None)
        assert strategy["env_type"] == EnvType.VENV
        assert "pip install" in strategy["install_steps"][1]

    def test_javascript_repo(self) -> None:
        info = {"language": "JavaScript", "topics": []}
        strategy = detect_install_strategy(info, None)
        assert strategy["env_type"] == EnvType.NODE
        assert "npm install" in strategy["install_steps"][0]

    def test_agent_category(self) -> None:
        info = {"language": "Python", "topics": ["ai-agent", "autonomous"]}
        strategy = detect_install_strategy(info, None)
        assert strategy["category"] == AppCategory.AGENT

    def test_model_server_category(self) -> None:
        info = {"language": "Python", "topics": ["llm", "inference"]}
        strategy = detect_install_strategy(info, None)
        assert strategy["category"] == AppCategory.MODEL_SERVER

    def test_unknown_language(self) -> None:
        info = {"language": "Rust", "topics": []}
        strategy = detect_install_strategy(info, None)
        assert strategy["env_type"] == EnvType.NONE


# ─── Install Pipeline ───────────────────────────────────────────────────────

class TestInstallPipeline:
    @patch("agents.core.app_installer.fetch_latest_release")
    @patch("agents.core.app_installer.fetch_repo_info")
    @patch("subprocess.run")
    def test_install_success(
        self, mock_run: MagicMock, mock_info: MagicMock, mock_release_fn: MagicMock,
        installer: AppInstaller, mock_release: ReleaseInfo,
    ) -> None:
        mock_release_fn.return_value = mock_release
        mock_info.return_value = {"language": "Python", "description": "Test app", "topics": []}
        mock_run.return_value = MagicMock(returncode=0)

        result = installer.install("owner/test-app")
        assert result["success"] is True
        assert result["name"] == "test-app"
        assert installer.count == 1

    @patch("agents.core.app_installer.fetch_latest_release")
    @patch("agents.core.app_installer.fetch_repo_info")
    @patch("subprocess.run")
    def test_install_duplicate(
        self, mock_run: MagicMock, mock_info: MagicMock, mock_release_fn: MagicMock,
        installer: AppInstaller,
    ) -> None:
        mock_release_fn.return_value = None
        mock_info.return_value = {"language": "Python", "topics": []}
        mock_run.return_value = MagicMock(returncode=0)

        installer.install("owner/app")
        result = installer.install("owner/app")
        assert result["success"] is False
        assert "already installed" in result["error"]

    @patch("agents.core.app_installer.fetch_latest_release")
    @patch("agents.core.app_installer.fetch_repo_info")
    @patch("subprocess.run")
    def test_install_force(
        self, mock_run: MagicMock, mock_info: MagicMock, mock_release_fn: MagicMock,
        installer: AppInstaller,
    ) -> None:
        mock_release_fn.return_value = None
        mock_info.return_value = {"language": "Python", "topics": []}
        mock_run.return_value = MagicMock(returncode=0)

        installer.install("owner/app")
        result = installer.install("owner/app", force=True)
        assert result["success"] is True


# ─── Uninstall ───────────────────────────────────────────────────────────────

class TestUninstall:
    @patch("agents.core.app_installer.fetch_latest_release")
    @patch("agents.core.app_installer.fetch_repo_info")
    @patch("subprocess.run")
    def test_uninstall(
        self, mock_run: MagicMock, mock_info: MagicMock, mock_release_fn: MagicMock,
        installer: AppInstaller,
    ) -> None:
        mock_release_fn.return_value = None
        mock_info.return_value = {"language": "Python", "topics": []}
        mock_run.return_value = MagicMock(returncode=0)

        installer.install("owner/app")
        result = installer.uninstall("app")
        assert result["success"] is True
        assert installer.count == 0

    def test_uninstall_not_found(self, installer: AppInstaller) -> None:
        result = installer.uninstall("nonexistent")
        assert result["success"] is False


# ─── Update ──────────────────────────────────────────────────────────────────

class TestUpdate:
    def test_update_not_found(self, installer: AppInstaller) -> None:
        result = installer.update("nonexistent")
        assert result["success"] is False


# ─── Registry Persistence ───────────────────────────────────────────────────

class TestRegistry:
    @patch("agents.core.app_installer.fetch_latest_release")
    @patch("agents.core.app_installer.fetch_repo_info")
    @patch("subprocess.run")
    def test_persist_and_reload(
        self, mock_run: MagicMock, mock_info: MagicMock, mock_release_fn: MagicMock,
        apps_dir: Path,
    ) -> None:
        mock_release_fn.return_value = None
        mock_info.return_value = {"language": "Python", "topics": []}
        mock_run.return_value = MagicMock(returncode=0)

        inst1 = AppInstaller(apps_dir=apps_dir)
        inst1.install("owner/my-app")
        assert inst1.count == 1

        inst2 = AppInstaller(apps_dir=apps_dir)
        assert inst2.count == 1
        apps = inst2.list_apps()
        assert apps[0]["name"] == "my-app"

    def test_empty_registry(self, installer: AppInstaller) -> None:
        assert installer.count == 0
        assert installer.list_apps() == []


# ─── Listing ─────────────────────────────────────────────────────────────────

class TestListing:
    @patch("agents.core.app_installer.fetch_latest_release")
    @patch("agents.core.app_installer.fetch_repo_info")
    @patch("subprocess.run")
    def test_list_apps(
        self, mock_run: MagicMock, mock_info: MagicMock, mock_release_fn: MagicMock,
        installer: AppInstaller,
    ) -> None:
        mock_release_fn.return_value = None
        mock_info.return_value = {"language": "Python", "topics": []}
        mock_run.return_value = MagicMock(returncode=0)

        installer.install("org/app1")
        installer.install("org/app2")
        apps = installer.list_apps()
        assert len(apps) == 2

    def test_get_app(self, installer: AppInstaller) -> None:
        assert installer.get_app("nonexistent") is None
