"""Tests for ACFS directory structure and manifest validation."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent

# During rapid prototyping, governance/security/observability are gitignored.
# Tests should only enforce directories that are always present.
_PROTOTYPING = os.environ.get("PROTOTYPING_MODE", "1") == "1"


def test_required_directories_exist() -> None:
    required_dirs = [
        "data/sqlite",
        "data/cold_archive",
        "data/quarantine_dumps",
        "agents",
    ]
    # Only check governance/security when NOT in prototyping mode
    if not _PROTOTYPING:
        required_dirs += ["governance", "security"]

    for d in required_dirs:
        assert (REPO_ROOT / d).is_dir(), f"Missing required directory: {d}"


def test_acfs_manifest_schema() -> None:
    manifest_path = REPO_ROOT / "acfs.manifest.yaml"
    assert manifest_path.exists(), "acfs.manifest.yaml missing"
    with manifest_path.open() as f:
        manifest = yaml.safe_load(f)
    assert "version" in manifest
    assert "directories" in manifest
    assert isinstance(manifest["directories"], list)
    assert "active_sha256_checksums" in manifest
    for entry in manifest["directories"]:
        assert "path" in entry, f"Directory entry missing 'path': {entry}"
        assert "permissions" in entry, f"Directory entry missing 'permissions': {entry}"


def test_gitkeep_files_exist() -> None:
    gitkeeps = [
        "data/sqlite/.gitkeep",
        "data/cold_archive/.gitkeep",
        "data/quarantine_dumps/.gitkeep",
        "data/align_staging/.gitkeep",
    ]
    for gk in gitkeeps:
        assert (REPO_ROOT / gk).exists(), f"Missing .gitkeep: {gk}"
