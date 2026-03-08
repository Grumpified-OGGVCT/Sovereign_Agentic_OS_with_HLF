"""
Tests for ACFS Semantic Merge Resolution.

Covers:
  - diff_worktree — listing changed files
  - detect_conflicts — file + region overlap detection
  - _parse_diff_regions — hunk header parsing
  - _regions_overlap — region intersection math
  - merge_worktree — merge attempt with conflict detection

Note: Tests that require actual Git repos use the same
temporary repo pattern as test_acfs_worktree.py.
"""

from __future__ import annotations

import os
import subprocess
import tempfile

from agents.core.acfs import ACFSWorktreeManager

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _init_test_repo() -> str:
    """Create a temporary Git repo with a single initial commit."""
    repo = tempfile.mkdtemp(prefix="acfs_merge_test_")
    subprocess.run(["git", "init", repo], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, check=True, capture_output=True,
    )
    # Initial commit
    readme = os.path.join(repo, "README.md")
    with open(readme, "w") as f:
        f.write("# Test\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial"],
        cwd=repo, check=True, capture_output=True,
    )
    return repo


def _write_file(repo: str, name: str, content: str) -> None:
    """Write a file in the given repo/worktree."""
    path = os.path.join(repo, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


# --------------------------------------------------------------------------- #
# Region Parsing (unit test — no Git needed)
# --------------------------------------------------------------------------- #


class TestDiffRegionParsing:
    """_parse_diff_regions and _regions_overlap are pure functions."""

    def test_parse_single_hunk(self) -> None:
        diff = "@@ -1,3 +1,5 @@\n+new_line1\n+new_line2\n"
        regions = ACFSWorktreeManager._parse_diff_regions(diff)
        assert regions == [(1, 5)]

    def test_parse_multiple_hunks(self) -> None:
        diff = (
            "@@ -1,3 +1,3 @@\n modified\n"
            "@@ -10,2 +10,4 @@\n added\n"
        )
        regions = ACFSWorktreeManager._parse_diff_regions(diff)
        assert len(regions) == 2
        assert regions[0] == (1, 3)
        assert regions[1] == (10, 13)

    def test_parse_single_line_hunk(self) -> None:
        diff = "@@ -5 +5 @@\n single\n"
        regions = ACFSWorktreeManager._parse_diff_regions(diff)
        assert regions == [(5, 5)]

    def test_regions_overlap_true(self) -> None:
        a = [(1, 10)]
        b = [(8, 15)]
        assert ACFSWorktreeManager._regions_overlap(a, b) is True

    def test_regions_overlap_false(self) -> None:
        a = [(1, 5)]
        b = [(6, 10)]
        assert ACFSWorktreeManager._regions_overlap(a, b) is False

    def test_regions_overlap_adjacent(self) -> None:
        """Adjacent regions (touching at boundary) do overlap."""
        a = [(1, 5)]
        b = [(5, 10)]
        assert ACFSWorktreeManager._regions_overlap(a, b) is True

    def test_regions_overlap_empty(self) -> None:
        assert ACFSWorktreeManager._regions_overlap([], [(1, 5)]) is False
        assert ACFSWorktreeManager._regions_overlap([(1, 5)], []) is False


# --------------------------------------------------------------------------- #
# Live Git Tests
# --------------------------------------------------------------------------- #


class TestDetectConflicts:
    """File-level conflict detection between worktrees."""

    def test_no_overlap(self) -> None:
        """Two worktrees changing different files have no conflicts."""
        repo = _init_test_repo()
        wt_base = tempfile.mkdtemp(prefix="acfs_wt_base_")
        mgr = ACFSWorktreeManager(repo_root=repo, worktree_base_dir=wt_base)

        wt_a = mgr.create_worktree("agent_a", "feature/a")
        wt_b = mgr.create_worktree("agent_b", "feature/b")

        # Agent A modifies file_a.py
        _write_file(wt_a, "file_a.py", "print('a')")
        mgr.shadow_commit(wt_a, "agent a work")

        # Agent B modifies file_b.py
        _write_file(wt_b, "file_b.py", "print('b')")
        mgr.shadow_commit(wt_b, "agent b work")

        result = mgr.detect_conflicts(wt_a, wt_b)
        assert result["conflicting_files"] == []
        assert len(result["safe_files_a"]) >= 1
        assert len(result["safe_files_b"]) >= 1
        assert result["all_auto_resolvable"] is True

    def test_overlapping_files(self) -> None:
        """Two worktrees changing the same file are flagged."""
        repo = _init_test_repo()

        # Create a shared file first
        _write_file(repo, "shared.py", "line1\nline2\nline3\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add shared"],
            cwd=repo, check=True, capture_output=True,
        )

        wt_base = tempfile.mkdtemp(prefix="acfs_wt_base_")
        mgr = ACFSWorktreeManager(repo_root=repo, worktree_base_dir=wt_base)

        wt_a = mgr.create_worktree("agent_a", "feat/a2")
        wt_b = mgr.create_worktree("agent_b", "feat/b2")

        # Both modify shared.py
        _write_file(wt_a, "shared.py", "modified_by_a\nline2\nline3\n")
        mgr.shadow_commit(wt_a, "agent a changes shared")

        _write_file(wt_b, "shared.py", "line1\nline2\nmodified_by_b\n")
        mgr.shadow_commit(wt_b, "agent b changes shared")

        result = mgr.detect_conflicts(wt_a, wt_b)
        assert "shared.py" in result["conflicting_files"]


class TestDiffWorktree:
    """diff_worktree returns changed files."""

    def test_lists_changes(self) -> None:
        repo = _init_test_repo()
        wt_base = tempfile.mkdtemp(prefix="acfs_wt_base_")
        mgr = ACFSWorktreeManager(repo_root=repo, worktree_base_dir=wt_base)

        wt = mgr.create_worktree("agent_diff", "feat/diff")
        _write_file(wt, "new_file.py", "content")
        mgr.shadow_commit(wt, "add new file")

        changes = mgr.diff_worktree(wt)
        file_names = [c["file"] for c in changes]
        assert "new_file.py" in file_names
