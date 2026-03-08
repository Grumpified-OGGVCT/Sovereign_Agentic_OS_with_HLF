"""
Tests for ACFS Worktree Manager — agent isolation via Git worktrees.

These tests use a temporary Git repository to verify worktree lifecycle
operations without touching the real project repository.

Covers:
  - Create worktree → verify directory exists
  - Destroy worktree → verify cleanup
  - Max worktree limit enforcement
  - Shadow commit with Merkle hash
  - List worktrees returns correct metadata
  - Cleanup stale worktrees
  - WorktreeInfo dataclass
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from agents.core.acfs import ACFSWorktreeManager, WorktreeInfo

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary Git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@sovereign.os"],
        cwd=str(repo), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "ACFS Test"],
        cwd=str(repo), check=True, capture_output=True,
    )
    # Create an initial commit so worktrees can branch from HEAD
    (repo / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=str(repo), check=True, capture_output=True,
    )
    return repo


@pytest.fixture
def wt_base(tmp_path: Path) -> Path:
    """Create a temporary directory for worktrees."""
    d = tmp_path / "worktrees"
    d.mkdir()
    return d


@pytest.fixture
def manager(tmp_git_repo: Path, wt_base: Path) -> ACFSWorktreeManager:
    """Create an ACFSWorktreeManager with a temp repo."""
    return ACFSWorktreeManager(
        repo_root=str(tmp_git_repo),
        max_worktrees=3,
        worktree_base_dir=str(wt_base),
    )


# --------------------------------------------------------------------------- #
# WorktreeInfo
# --------------------------------------------------------------------------- #


class TestWorktreeInfo:
    """WorktreeInfo dataclass stores correct metadata."""

    def test_defaults(self) -> None:
        info = WorktreeInfo(path="/tmp/wt", branch="fix/test", agent_id="sentinel")
        assert info.commit_count == 0
        assert info.created_at > 0

    def test_fields(self) -> None:
        info = WorktreeInfo(
            path="/tmp/wt", branch="feat/auth", agent_id="catalyst", commit_count=5,
        )
        assert info.agent_id == "catalyst"
        assert info.commit_count == 5


# --------------------------------------------------------------------------- #
# Create Worktree
# --------------------------------------------------------------------------- #


class TestCreateWorktree:
    """ACFSWorktreeManager creates worktrees correctly."""

    def test_create_worktree_returns_path(self, manager: ACFSWorktreeManager) -> None:
        """create_worktree returns a valid directory path."""
        path = manager.create_worktree("sentinel", "fix/ssrf")
        assert Path(path).exists()
        assert Path(path).is_dir()

    def test_create_worktree_has_git(self, manager: ACFSWorktreeManager) -> None:
        """Created worktree has a .git file (not directory — it's a linked worktree)."""
        path = manager.create_worktree("catalyst", "perf/cache")
        git_path = Path(path) / ".git"
        assert git_path.exists()

    def test_create_worktree_tracked(self, manager: ACFSWorktreeManager) -> None:
        """Created worktree is tracked in internal registry."""
        path = manager.create_worktree("scribe", "audit/gas")
        wts = manager.list_worktrees()
        assert len(wts) == 1
        assert wts[0]["path"] == path
        assert wts[0]["agent_id"] == "scribe"

    def test_max_worktrees_enforced(self, manager: ACFSWorktreeManager) -> None:
        """Cannot exceed max_worktrees limit."""
        manager.create_worktree("a1", "branch/a1")
        manager.create_worktree("a2", "branch/a2")
        manager.create_worktree("a3", "branch/a3")

        with pytest.raises(RuntimeError, match="limit reached"):
            manager.create_worktree("a4", "branch/a4")


# --------------------------------------------------------------------------- #
# Destroy Worktree
# --------------------------------------------------------------------------- #


class TestDestroyWorktree:
    """ACFSWorktreeManager destroys worktrees correctly."""

    def test_destroy_removes_directory(self, manager: ACFSWorktreeManager) -> None:
        """Destroy removes the worktree directory."""
        path = manager.create_worktree("sentinel", "fix/destroy")
        assert Path(path).exists()

        manager.destroy_worktree(path)
        assert not Path(path).exists()

    def test_destroy_removes_from_registry(self, manager: ACFSWorktreeManager) -> None:
        """Destroy removes the worktree from internal tracking."""
        path = manager.create_worktree("sentinel", "fix/cleanup")
        assert len(manager.list_worktrees()) == 1

        manager.destroy_worktree(path)
        assert len(manager.list_worktrees()) == 0


# --------------------------------------------------------------------------- #
# Shadow Commit
# --------------------------------------------------------------------------- #


class TestShadowCommit:
    """ACFSWorktreeManager creates shadow commits with Merkle hashes."""

    def test_shadow_commit_returns_sha(self, manager: ACFSWorktreeManager) -> None:
        """shadow_commit returns a valid Git SHA."""
        path = manager.create_worktree("sentinel", "fix/commit-test")

        # Create a file in the worktree
        (Path(path) / "test_file.txt").write_text("hello from agent\n")

        sha = manager.shadow_commit(path, "sentinel: test commit")
        assert len(sha) == 40  # Full SHA-1
        assert all(c in "0123456789abcdef" for c in sha)

    def test_shadow_commit_includes_merkle(
        self, manager: ACFSWorktreeManager
    ) -> None:
        """Commit message includes ALIGN-Merkle hash."""
        path = manager.create_worktree("catalyst", "fix/merkle-test")
        (Path(path) / "data.json").write_text('{"key": "value"}\n')

        manager.shadow_commit(path, "catalyst: merkle test")

        # Verify commit message contains Merkle hash
        result = subprocess.run(
            ["git", "log", "-1", "--format=%B"],
            cwd=path, capture_output=True, text=True,
        )
        assert "ALIGN-Merkle:" in result.stdout

    def test_shadow_commit_increments_count(
        self, manager: ACFSWorktreeManager
    ) -> None:
        """Each shadow_commit increments the commit_count."""
        path = manager.create_worktree("scribe", "audit/count-test")
        (Path(path) / "f1.txt").write_text("first\n")
        manager.shadow_commit(path, "commit 1")
        (Path(path) / "f2.txt").write_text("second\n")
        manager.shadow_commit(path, "commit 2")

        wts = manager.list_worktrees()
        assert wts[0]["commit_count"] == 2


# --------------------------------------------------------------------------- #
# List Worktrees
# --------------------------------------------------------------------------- #


class TestListWorktrees:
    """list_worktrees returns correct metadata."""

    def test_empty_list(self, manager: ACFSWorktreeManager) -> None:
        """No worktrees returns empty list."""
        assert manager.list_worktrees() == []

    def test_multiple_worktrees(self, manager: ACFSWorktreeManager) -> None:
        """Multiple worktrees returned correctly."""
        manager.create_worktree("sentinel", "branch/s1")
        manager.create_worktree("catalyst", "branch/c1")

        wts = manager.list_worktrees()
        assert len(wts) == 2
        agents = {wt["agent_id"] for wt in wts}
        assert agents == {"sentinel", "catalyst"}


# --------------------------------------------------------------------------- #
# Cleanup Stale
# --------------------------------------------------------------------------- #


class TestCleanupStale:
    """cleanup_stale removes old worktrees."""

    def test_cleanup_stale_removes_old(self, manager: ACFSWorktreeManager) -> None:
        """Worktrees older than threshold are cleaned up."""
        path = manager.create_worktree("old-agent", "branch/old")

        # Artificially age the worktree
        info = manager._worktrees[path]
        info.created_at = time.time() - (25 * 3600)  # 25 hours old

        cleaned = manager.cleanup_stale()
        assert len(cleaned) == 1
        assert path in cleaned
        assert len(manager.list_worktrees()) == 0

    def test_cleanup_stale_keeps_fresh(self, manager: ACFSWorktreeManager) -> None:
        """Fresh worktrees are not cleaned up."""
        manager.create_worktree("fresh-agent", "branch/fresh")

        cleaned = manager.cleanup_stale()
        assert len(cleaned) == 0
        assert len(manager.list_worktrees()) == 1
