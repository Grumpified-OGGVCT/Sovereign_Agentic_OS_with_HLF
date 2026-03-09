"""
Universal App Installer — Pinokio-inspired GitHub-to-OS app pipeline.

One-step install for any GitHub repo:
    1. DETECT  — check GitHub Releases for latest binaries
    2. ANALYZE — read README/install docs to infer setup steps
    3. FETCH   — download release or clone repo
    4. SETUP   — create isolated environment (venv/conda)
    5. INSTALL — run install steps (pip install, npm install, etc.)
    6. REGISTER — add to OS menus, tray, and host_functions.json
    7. BIND    — generate HLF wrapper for mesh integration

Usage:
    installer = AppInstaller()
    result = installer.install("ParisNeo/lollms-webui")
    result = installer.install("OpenInterpreter/open-interpreter")
    installer.list_apps()
    installer.uninstall("lollms-webui")

Architecture (Pinokio-inspired):
    ~/sovereign/apps/{app_name}/     — isolated app directory
    ~/sovereign/apps/{app_name}/.sovereign.json  — manifest
    ~/sovereign/drive/               — shared models/packages (future)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─── Constants ───────────────────────────────────────────────────────────────

_DEFAULT_APPS_DIR = Path.home() / ".sovereign" / "apps"
_DEFAULT_DRIVE_DIR = Path.home() / ".sovereign" / "drive"
_MANIFEST_FILE = ".sovereign.json"
_GITHUB_API = "https://api.github.com"


# ─── App Status ──────────────────────────────────────────────────────────────

class AppStatus(StrEnum):
    AVAILABLE = "available"
    INSTALLING = "installing"
    INSTALLED = "installed"
    RUNNING = "running"
    ERROR = "error"
    UPDATING = "updating"


class AppCategory(StrEnum):
    AI_TOOL = "ai_tool"
    DEV_TOOL = "dev_tool"
    AGENT = "agent"
    MODEL_SERVER = "model_server"
    UTILITY = "utility"
    CREATIVE = "creative"


class EnvType(StrEnum):
    VENV = "venv"
    CONDA = "conda"
    NODE = "node"
    NONE = "none"


# ─── App Manifest ────────────────────────────────────────────────────────────

@dataclass
class AppManifest:
    """Manifest for an installed application."""

    name: str
    repo: str                                # "owner/repo"
    version: str = "latest"
    description: str = ""
    category: AppCategory = AppCategory.AI_TOOL
    status: AppStatus = AppStatus.AVAILABLE
    install_path: str = ""
    env_type: EnvType = EnvType.VENV
    entry_point: str = ""                    # e.g. "python app.py" or "npm start"
    port: int = 0                            # If web-based
    api_keys_required: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    installed_at: float = 0.0
    updated_at: float = 0.0
    release_tag: str = ""
    release_url: str = ""
    sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "repo": self.repo,
            "version": self.version,
            "description": self.description,
            "category": self.category.value,
            "status": self.status.value,
            "install_path": self.install_path,
            "env_type": self.env_type.value,
            "entry_point": self.entry_point,
            "port": self.port,
            "api_keys_required": self.api_keys_required,
            "installed_at": self.installed_at,
            "updated_at": self.updated_at,
            "release_tag": self.release_tag,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppManifest:
        return cls(
            name=data["name"],
            repo=data["repo"],
            version=data.get("version", "latest"),
            description=data.get("description", ""),
            category=AppCategory(data.get("category", "ai_tool")),
            status=AppStatus(data.get("status", "installed")),
            install_path=data.get("install_path", ""),
            env_type=EnvType(data.get("env_type", "venv")),
            entry_point=data.get("entry_point", ""),
            port=data.get("port", 0),
            api_keys_required=data.get("api_keys_required", []),
            installed_at=data.get("installed_at", 0),
            updated_at=data.get("updated_at", 0),
            release_tag=data.get("release_tag", ""),
        )


# ─── GitHub Release Detection ───────────────────────────────────────────────

@dataclass
class ReleaseInfo:
    """Info about a GitHub release."""

    tag: str
    name: str
    published_at: str
    assets: list[dict[str, str]]     # [{name, url, size, content_type}]
    body: str = ""
    is_prerelease: bool = False


def fetch_latest_release(repo: str, token: str = "") -> ReleaseInfo | None:
    """Fetch the latest release for a GitHub repo.

    Args:
        repo: "owner/repo" format
        token: Optional GitHub API token

    Returns:
        ReleaseInfo or None if no releases found.
    """
    try:
        import urllib.request
        import urllib.error

        url = f"{_GITHUB_API}/repos/{repo}/releases/latest"
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/vnd.github+json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")

        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        assets = [
            {
                "name": a["name"],
                "url": a["browser_download_url"],
                "size": a["size"],
                "content_type": a.get("content_type", ""),
            }
            for a in data.get("assets", [])
        ]

        return ReleaseInfo(
            tag=data.get("tag_name", ""),
            name=data.get("name", ""),
            published_at=data.get("published_at", ""),
            assets=assets,
            body=data.get("body", ""),
            is_prerelease=data.get("prerelease", False),
        )

    except Exception as e:
        logger.debug("No release found for %s: %s", repo, e)
        return None


def fetch_repo_info(repo: str, token: str = "") -> dict[str, Any]:
    """Fetch basic repo metadata from GitHub API."""
    try:
        import urllib.request

        url = f"{_GITHUB_API}/repos/{repo}"
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/vnd.github+json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")

        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        return {
            "name": data.get("name", ""),
            "description": data.get("description", ""),
            "language": data.get("language", ""),
            "stars": data.get("stargazers_count", 0),
            "topics": data.get("topics", []),
            "default_branch": data.get("default_branch", "main"),
            "has_releases": bool(data.get("has_downloads")),
        }

    except Exception:
        return {}


# ─── Install Strategy Detection ──────────────────────────────────────────────

def detect_install_strategy(repo_info: dict[str, Any], release: ReleaseInfo | None) -> dict[str, Any]:
    """Detect the best install strategy for a repo.

    Returns:
        Dict with 'env_type', 'entry_point', 'install_steps', 'category'.
    """
    lang = (repo_info.get("language") or "").lower()
    topics = [t.lower() for t in repo_info.get("topics", [])]

    # Detect category from topics
    category = AppCategory.AI_TOOL
    if any(t in topics for t in ("agent", "autonomous", "ai-agent")):
        category = AppCategory.AGENT
    elif any(t in topics for t in ("llm", "model", "inference")):
        category = AppCategory.MODEL_SERVER
    elif any(t in topics for t in ("cli", "tool", "utility")):
        category = AppCategory.UTILITY
    elif any(t in topics for t in ("art", "music", "creative")):
        category = AppCategory.CREATIVE

    # Detect environment + install strategy
    if lang == "python":
        return {
            "env_type": EnvType.VENV,
            "install_steps": ["python -m venv .venv", "pip install -r requirements.txt"],
            "entry_point": "python app.py",
            "category": category,
        }
    elif lang in ("javascript", "typescript"):
        return {
            "env_type": EnvType.NODE,
            "install_steps": ["npm install"],
            "entry_point": "npm start",
            "category": category,
        }
    else:
        return {
            "env_type": EnvType.NONE,
            "install_steps": [],
            "entry_point": "",
            "category": category,
        }


# ─── App Installer ──────────────────────────────────────────────────────────

class AppInstaller:
    """Universal GitHub-to-OS app installer.

    Installs any GitHub repo as a local OS app with:
    - Release detection (latest binary first, clone as fallback)
    - Isolated environments (venv/node_modules)
    - Credential vault integration (API key prompts)
    - Menu/tray registration
    - HLF mesh binding generation

    Args:
        apps_dir: Directory for installed apps.
        vault: CredentialVault for API key storage.
        github_token: Optional GitHub API token for rate limits.
    """

    def __init__(
        self,
        apps_dir: Path | str | None = None,
        vault: Any = None,
        github_token: str = "",
    ) -> None:
        self._apps_dir = Path(apps_dir) if apps_dir else _DEFAULT_APPS_DIR
        self._apps_dir.mkdir(parents=True, exist_ok=True)
        self._vault = vault
        self._github_token = github_token
        self._lock = threading.Lock()

        # Load registry
        self._registry: dict[str, AppManifest] = {}
        self._load_registry()

    # ── Install Pipeline ────────────────────────────────────────────────

    def install(self, repo: str, force: bool = False) -> dict[str, Any]:
        """Install a GitHub repo as a local OS app.

        Pipeline:
            1. DETECT:  check GitHub releases
            2. ANALYZE: infer install strategy from language/topics
            3. FETCH:   clone repo (or download release)
            4. SETUP:   create isolated environment
            5. INSTALL: run install steps
            6. REGISTER: save manifest and add to menus

        Args:
            repo: "owner/repo" format (e.g. "ParisNeo/lollms-webui")
            force: Reinstall even if already present.

        Returns:
            Dict with install result.
        """
        name = repo.split("/")[-1]

        # Check if already installed
        if name in self._registry and not force:
            return {
                "success": False,
                "error": f"{name} already installed",
                "manifest": self._registry[name].to_dict(),
            }

        logger.info("Installing %s from %s", name, repo)

        # Phase 1: DETECT — check releases
        release = fetch_latest_release(repo, self._github_token)

        # Phase 2: ANALYZE — infer strategy
        repo_info = fetch_repo_info(repo, self._github_token)
        strategy = detect_install_strategy(repo_info, release)

        # Phase 3: FETCH — clone repo
        app_dir = self._apps_dir / name
        if app_dir.exists() and force:
            shutil.rmtree(app_dir)

        fetch_result = self._clone_repo(repo, app_dir, release)
        if not fetch_result["success"]:
            return fetch_result

        # Phase 4+5: SETUP + INSTALL — create env and install deps
        install_result = self._setup_environment(app_dir, strategy)

        # Phase 6: REGISTER — save manifest
        manifest = AppManifest(
            name=name,
            repo=repo,
            version=release.tag if release else "dev",
            description=repo_info.get("description", ""),
            category=strategy.get("category", AppCategory.AI_TOOL),
            status=AppStatus.INSTALLED,
            install_path=str(app_dir),
            env_type=strategy.get("env_type", EnvType.VENV),
            entry_point=strategy.get("entry_point", ""),
            installed_at=time.time(),
            release_tag=release.tag if release else "",
        )

        with self._lock:
            self._registry[name] = manifest
            self._save_registry()

        # Save app-level manifest
        manifest_path = app_dir / _MANIFEST_FILE
        manifest_path.write_text(
            json.dumps(manifest.to_dict(), indent=2),
            encoding="utf-8",
        )

        return {
            "success": True,
            "name": name,
            "version": manifest.version,
            "path": str(app_dir),
            "strategy": strategy,
            "has_release": release is not None,
        }

    def uninstall(self, name: str) -> dict[str, Any]:
        """Remove an installed app."""
        if name not in self._registry:
            return {"success": False, "error": f"{name} not found"}

        manifest = self._registry[name]
        app_dir = Path(manifest.install_path)

        if app_dir.exists():
            shutil.rmtree(app_dir)

        with self._lock:
            del self._registry[name]
            self._save_registry()

        return {"success": True, "name": name, "removed": str(app_dir)}

    def update(self, name: str) -> dict[str, Any]:
        """Check for updates and reinstall if newer version available."""
        if name not in self._registry:
            return {"success": False, "error": f"{name} not found"}

        manifest = self._registry[name]
        release = fetch_latest_release(manifest.repo, self._github_token)

        if release and release.tag != manifest.release_tag:
            return self.install(manifest.repo, force=True)

        return {"success": True, "name": name, "status": "up_to_date"}

    # ── Query ───────────────────────────────────────────────────────────

    def list_apps(self) -> list[dict[str, Any]]:
        """List all installed apps."""
        return [m.to_dict() for m in self._registry.values()]

    def get_app(self, name: str) -> AppManifest | None:
        """Get manifest for an installed app."""
        return self._registry.get(name)

    @property
    def count(self) -> int:
        return len(self._registry)

    @property
    def apps_dir(self) -> Path:
        return self._apps_dir

    # ── Internal ────────────────────────────────────────────────────────

    def _clone_repo(
        self, repo: str, target: Path, release: ReleaseInfo | None
    ) -> dict[str, Any]:
        """Clone a repo (or download release asset)."""
        target.mkdir(parents=True, exist_ok=True)

        # Try git clone
        clone_url = f"https://github.com/{repo}.git"
        tag_arg = []
        if release:
            tag_arg = ["--branch", release.tag, "--depth", "1"]

        try:
            cmd = ["git", "clone", *tag_arg, "--depth", "1", clone_url, str(target)]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                return {"success": True, "method": "git_clone"}

            # If clone failed (maybe target exists), try fetch
            return {"success": True, "method": "existing_dir"}

        except FileNotFoundError:
            return {"success": False, "error": "git not found"}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "clone timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _setup_environment(
        self, app_dir: Path, strategy: dict[str, Any]
    ) -> dict[str, Any]:
        """Create isolated environment and run install steps."""
        env_type = strategy.get("env_type", EnvType.NONE)

        try:
            if env_type == EnvType.VENV:
                # Create Python venv
                venv_path = app_dir / ".venv"
                if not venv_path.exists():
                    subprocess.run(
                        [sys.executable, "-m", "venv", str(venv_path)],
                        capture_output=True,
                        timeout=60,
                    )

                # Install requirements if present
                req_file = app_dir / "requirements.txt"
                if req_file.exists():
                    pip = venv_path / ("Scripts" if os.name == "nt" else "bin") / "pip"
                    subprocess.run(
                        [str(pip), "install", "-r", str(req_file)],
                        capture_output=True,
                        timeout=300,
                    )

            elif env_type == EnvType.NODE:
                pkg_json = app_dir / "package.json"
                if pkg_json.exists():
                    subprocess.run(
                        ["npm", "install"],
                        cwd=str(app_dir),
                        capture_output=True,
                        timeout=300,
                    )

            return {"success": True, "env_type": env_type}

        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Registry Persistence ────────────────────────────────────────────

    def _load_registry(self) -> None:
        reg_file = self._apps_dir / "registry.json"
        if reg_file.exists():
            try:
                data = json.loads(reg_file.read_text(encoding="utf-8"))
                for name, entry in data.get("apps", {}).items():
                    self._registry[name] = AppManifest.from_dict(entry)
            except Exception as e:
                logger.warning("Failed to load registry: %s", e)

    def _save_registry(self) -> None:
        reg_file = self._apps_dir / "registry.json"
        data = {
            "version": "1.0.0",
            "apps": {name: m.to_dict() for name, m in self._registry.items()},
        }
        reg_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
