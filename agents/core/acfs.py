"""
ACFS Worktree Manager — Git worktree isolation for parallel agent execution.

Provides automated Git worktree management so agents can work in parallel
without file-locking conflicts.  Each agent gets a physically isolated
directory that shares the same .git object store.

Usage::

    from agents.core.acfs import ACFSWorktreeManager

    mgr = ACFSWorktreeManager(repo_root=".")
    path = mgr.create_worktree("sentinel-01", "fix/ssrf-defense")
    # ... agent does work in `path` ...
    mgr.shadow_commit(path, "sentinel: hardened SSRF filter")
    mgr.destroy_worktree(path)

All operations log to the ALIGN Ledger for forensic traceability.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default limits
_DEFAULT_MAX_WORKTREES = 8
_DEFAULT_AUTO_CLEANUP_HOURS = 24


@dataclass
class WorktreeInfo:
    """Metadata about an active worktree."""
    path: str
    branch: str
    agent_id: str
    created_at: float = field(default_factory=time.time)
    commit_count: int = 0


class ACFSWorktreeManager:
    """Git worktree lifecycle manager with ALIGN ledger integration.

    Attributes:
        repo_root: Absolute path to the Git repository root
        max_worktrees: Maximum number of concurrent worktrees
        worktree_base_dir: Directory under which worktrees are created
        auto_cleanup_hours: Hours after which stale worktrees are cleaned
    """

    def __init__(
        self,
        repo_root: str | Path = ".",
        max_worktrees: int = _DEFAULT_MAX_WORKTREES,
        worktree_base_dir: str | Path | None = None,
        auto_cleanup_hours: int = _DEFAULT_AUTO_CLEANUP_HOURS,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.max_worktrees = max_worktrees
        self.auto_cleanup_hours = auto_cleanup_hours
        self._worktrees: dict[str, WorktreeInfo] = {}

        if worktree_base_dir is None:
            self.worktree_base_dir = Path(tempfile.mkdtemp(prefix="acfs_wt_"))
        else:
            self.worktree_base_dir = Path(worktree_base_dir).resolve()
            self.worktree_base_dir.mkdir(parents=True, exist_ok=True)

    def create_worktree(self, agent_id: str, branch_name: str) -> str:
        """Create a new Git worktree for an agent.

        Args:
            agent_id: Unique identifier for the agent
            branch_name: Branch name for the worktree

        Returns:
            Absolute path to the created worktree directory

        Raises:
            RuntimeError: If max worktree limit reached or git fails
        """
        if len(self._worktrees) >= self.max_worktrees:
            raise RuntimeError(
                f"ACFS worktree limit reached ({self.max_worktrees}). "
                f"Destroy existing worktrees first."
            )

        # Create unique directory name
        wt_dir = self.worktree_base_dir / f"wt_{agent_id}_{branch_name.replace('/', '_')}"

        try:
            # Create the worktree with a new branch
            self._run_git(
                ["worktree", "add", "-b", branch_name, str(wt_dir)],
                check=True,
            )
        except subprocess.CalledProcessError:
            # Branch may already exist — try without -b
            try:
                self._run_git(
                    ["worktree", "add", str(wt_dir), branch_name],
                    check=True,
                )
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"ACFS: failed to create worktree: {e}") from e

        wt_path = str(wt_dir.resolve())
        info = WorktreeInfo(
            path=wt_path,
            branch=branch_name,
            agent_id=agent_id,
        )
        self._worktrees[wt_path] = info

        self._log_align("ACFS_WORKTREE_CREATED", {
            "agent_id": agent_id,
            "branch": branch_name,
            "path": wt_path,
        })

        logger.info(f"ACFS: created worktree for {agent_id} at {wt_path}")
        return wt_path

    def destroy_worktree(self, worktree_path: str) -> None:
        """Remove a Git worktree and clean up.

        Args:
            worktree_path: Path to the worktree to remove

        Raises:
            RuntimeError: If the worktree doesn't exist or git fails
        """
        info = self._worktrees.get(worktree_path)
        agent_id = info.agent_id if info else "unknown"

        try:
            self._run_git(
                ["worktree", "remove", "--force", worktree_path],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ACFS: failed to remove worktree: {e}") from e

        self._worktrees.pop(worktree_path, None)

        self._log_align("ACFS_WORKTREE_DESTROYED", {
            "agent_id": agent_id,
            "path": worktree_path,
        })

        logger.info(f"ACFS: destroyed worktree at {worktree_path}")

    def list_worktrees(self) -> list[dict[str, Any]]:
        """Return all active worktrees with their agent assignments.

        Returns:
            List of dicts with path, branch, agent_id, created_at, commit_count
        """
        return [
            {
                "path": info.path,
                "branch": info.branch,
                "agent_id": info.agent_id,
                "created_at": info.created_at,
                "commit_count": info.commit_count,
            }
            for info in self._worktrees.values()
        ]

    def shadow_commit(self, worktree_path: str, message: str) -> str:
        """Commit all changes in a worktree with ALIGN Merkle hash.

        Args:
            worktree_path: Path to the worktree
            message: Commit message

        Returns:
            The commit SHA

        Raises:
            RuntimeError: If commit fails
        """
        try:
            # Stage all changes
            self._run_git(["add", "-A"], cwd=worktree_path, check=True)

            # Compute ALIGN-compatible Merkle hash of staged content
            diff_output = self._run_git(
                ["diff", "--cached", "--stat"],
                cwd=worktree_path,
            )
            merkle_hash = hashlib.sha256(
                diff_output.encode() if diff_output else b""
            ).hexdigest()[:16]

            # Commit with Merkle hash in message
            full_message = f"{message}\n\nALIGN-Merkle: {merkle_hash}"
            self._run_git(
                ["commit", "-m", full_message, "--allow-empty"],
                cwd=worktree_path,
                check=True,
            )

            # Get the commit SHA
            sha = self._run_git(
                ["rev-parse", "HEAD"],
                cwd=worktree_path,
            ).strip()

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ACFS: shadow commit failed: {e}") from e

        # Track commit count
        info = self._worktrees.get(worktree_path)
        if info:
            info.commit_count += 1

        self._log_align("ACFS_SHADOW_COMMIT", {
            "path": worktree_path,
            "agent_id": info.agent_id if info else "unknown",
            "sha": sha,
            "merkle_hash": merkle_hash,
            "message": message,
        })

        logger.info(f"ACFS: shadow commit {sha[:8]} in {worktree_path}")
        return sha

    def cleanup_stale(self) -> list[str]:
        """Remove worktrees older than auto_cleanup_hours.

        Returns:
            List of paths that were cleaned up
        """
        cutoff = time.time() - (self.auto_cleanup_hours * 3600)
        stale = [
            path for path, info in self._worktrees.items()
            if info.created_at < cutoff
        ]

        for path in stale:
            try:
                self.destroy_worktree(path)
            except RuntimeError:
                logger.warning(f"ACFS: failed to clean stale worktree {path}")

        return stale

    # ------------------------------------------------------------------ #
    # Semantic Merge Resolution
    # ------------------------------------------------------------------ #

    def diff_worktree(self, worktree_path: str) -> list[dict]:
        """List files changed in a worktree relative to its upstream branch.

        Returns:
            List of dicts with 'status' (M/A/D/R) and 'file' path.
        """
        try:
            output = self._run_git(
                ["diff", "--name-status", "HEAD~1..HEAD"],
                cwd=worktree_path,
            )
        except Exception:
            output = self._run_git(
                ["status", "--porcelain"],
                cwd=worktree_path,
            )

        changes = []
        for line in output.strip().splitlines():
            if not line.strip():
                continue
            parts = line.strip().split(None, 1)
            if len(parts) == 2:
                changes.append({
                    "status": parts[0],
                    "file": parts[1],
                })
        return changes

    def detect_conflicts(
        self, wt_a: str, wt_b: str
    ) -> dict:
        """Detect potential conflicts between two worktrees.

        Compares the set of modified files in each worktree to identify
        overlapping changes at the file level. For overlapping files,
        attempts function-level detection by comparing diff regions.

        Args:
            wt_a: Path to first worktree.
            wt_b: Path to second worktree.

        Returns:
            Dict with 'conflicting_files', 'safe_files_a', 'safe_files_b',
            and 'overlap_details'.
        """
        changes_a = {c["file"] for c in self.diff_worktree(wt_a)}
        changes_b = {c["file"] for c in self.diff_worktree(wt_b)}

        overlap = changes_a & changes_b
        safe_a = changes_a - overlap
        safe_b = changes_b - overlap

        # For overlapping files, try to get diff regions
        overlap_details = []
        for filepath in overlap:
            detail = {
                "file": filepath,
                "regions_a": [],
                "regions_b": [],
                "auto_resolvable": False,
            }

            # Get diff hunks from each worktree
            for label, wt_path in [("a", wt_a), ("b", wt_b)]:
                try:
                    diff = self._run_git(
                        ["diff", "HEAD~1..HEAD", "--unified=0", "--", filepath],
                        cwd=wt_path,
                    )
                    regions = self._parse_diff_regions(diff)
                    detail[f"regions_{label}"] = regions
                except Exception:
                    # Diff collection may fail for binary files or shallow clones
                    logger.debug(
                        "Failed to collect diff for %s in worktree %s",
                        filepath, label,
                    )

            # If diff regions don't overlap, it's auto-resolvable
            if detail["regions_a"] and detail["regions_b"]:
                detail["auto_resolvable"] = not self._regions_overlap(
                    detail["regions_a"], detail["regions_b"]
                )

            overlap_details.append(detail)

        self._log_align("ACFS_CONFLICT_DETECTION", {
            "wt_a": wt_a,
            "wt_b": wt_b,
            "conflicting_files": list(overlap),
            "safe_a": len(safe_a),
            "safe_b": len(safe_b),
        })

        return {
            "conflicting_files": list(overlap),
            "safe_files_a": list(safe_a),
            "safe_files_b": list(safe_b),
            "overlap_details": overlap_details,
            "all_auto_resolvable": all(
                d["auto_resolvable"] for d in overlap_details
            ) if overlap_details else True,
        }

    def merge_worktree(
        self, worktree_path: str, target_branch: str = "main"
    ) -> dict:
        """Merge a worktree branch into a target branch.

        Attempts a git merge. If conflicts occur, reports them
        without auto-resolving (escalate to CoVE for review).

        Args:
            worktree_path: Path to the worktree to merge.
            target_branch: Branch to merge into (default: main).

        Returns:
            Dict with 'success', 'merge_sha', 'conflicts', 'files_changed'.
        """
        info = self._worktrees.get(worktree_path)
        source_branch = info.branch if info else "unknown"

        try:
            # Switch to target branch in the repo root
            self._run_git(["checkout", target_branch], check=True)

            # Attempt merge
            result = subprocess.run(
                ["git", "merge", source_branch, "--no-edit"],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                sha = self._run_git(["rev-parse", "HEAD"]).strip()
                # Count files changed
                files_output = self._run_git(
                    ["diff", "--name-only", "HEAD~1..HEAD"],
                )
                files_changed = [
                    f for f in files_output.strip().splitlines() if f.strip()
                ]

                self._log_align("ACFS_MERGE_SUCCESS", {
                    "source": source_branch,
                    "target": target_branch,
                    "sha": sha,
                    "files_changed": len(files_changed),
                })

                return {
                    "success": True,
                    "merge_sha": sha,
                    "conflicts": [],
                    "files_changed": files_changed,
                }
            else:
                # Merge conflict — parse conflicting files
                conflicts = []
                if result.stdout:
                    for line in result.stdout.splitlines():
                        if "CONFLICT" in line:
                            conflicts.append(line.strip())

                # Abort the merge to leave repo clean
                self._run_git(["merge", "--abort"])

                self._log_align("ACFS_MERGE_CONFLICT", {
                    "source": source_branch,
                    "target": target_branch,
                    "conflicts": conflicts,
                })

                return {
                    "success": False,
                    "merge_sha": None,
                    "conflicts": conflicts,
                    "files_changed": [],
                }

        except subprocess.CalledProcessError as e:
            self._run_git(["merge", "--abort"])
            return {
                "success": False,
                "merge_sha": None,
                "conflicts": [str(e)],
                "files_changed": [],
            }

    # ------------------------------------------------------------------ #
    # Diff region parsing helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_diff_regions(diff_text: str) -> list[tuple[int, int]]:
        """Parse diff hunk headers to extract modified line regions.

        Returns:
            List of (start_line, end_line) tuples for modified regions.
        """
        regions = []
        for line in diff_text.splitlines():
            if line.startswith("@@"):
                # Parse @@ -old_start,old_count +new_start,new_count @@
                try:
                    parts = line.split("@@")[1].strip()
                    new_part = parts.split("+")[1].split()[0]
                    if "," in new_part:
                        start = int(new_part.split(",")[0])
                        count = int(new_part.split(",")[1])
                    else:
                        start = int(new_part)
                        count = 1
                    regions.append((start, start + count - 1))
                except (IndexError, ValueError):
                    # Skip malformed hunk headers in diff output
                    logger.debug("Skipping malformed hunk header: %s", line)
        return regions

    @staticmethod
    def _regions_overlap(
        regions_a: list[tuple[int, int]],
        regions_b: list[tuple[int, int]],
    ) -> bool:
        """Check if any diff regions overlap between two sets.

        Returns True if any region in A overlaps with any region in B.
        """
        for a_start, a_end in regions_a:
            for b_start, b_end in regions_b:
                if a_start <= b_end and b_start <= a_end:
                    return True
        return False

    # --- Internal helpers ---

    def _run_git(
        self,
        args: list[str],
        cwd: str | None = None,
        check: bool = False,
    ) -> str:
        """Run a git command and return stdout."""
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or str(self.repo_root),
            capture_output=True,
            text=True,
            check=check,
        )
        return result.stdout

    def _log_align(self, event: str, data: dict) -> None:
        """Log an event to the ALIGN ledger."""
        try:
            from agents.core.als_logger import ALSLogger
            als = ALSLogger()
            als.log(event, data)
        except ImportError:
            pass  # Standalone mode
