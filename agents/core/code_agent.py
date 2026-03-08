"""
Code Agent — Reads, Writes, and Refactors Source Code in Sandbox.

The "hands" of the agent system.  Operates exclusively through
AgentSandbox file operations, ensuring all changes are path-safe
and fully audited via the ALIGN ledger.

Task types:
  - create_file: Generate a new file from a spec description
  - modify_file: Apply targeted changes to an existing file
  - refactor: Bulk rename / restructure across multiple files
  - delete_file: Remove a file from the worktree

Usage::

    from agents.core.code_agent import CodeAgent

    agent = CodeAgent(agent_id="code-01", agent_role="developer")
    result = agent.execute_task(
        {"type": "create_file", "path": "src/auth.py", "content": "..."},
        sandbox=sandbox,
    )
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from agents.core.agent_sandbox import AgentSandbox

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Task Result
# --------------------------------------------------------------------------- #


@dataclass
class TaskResult:
    """Result of a code agent task execution.

    Attributes:
        success: Whether the task completed without error.
        task_type: The type of task that was executed.
        files_modified: List of file paths that were changed.
        diff_summary: Human-readable summary of changes.
        error: Error message if failed.
        duration: Execution time in seconds.
        action_count: Number of sandbox actions taken.
        metadata: Extra context from the task.
    """
    success: bool
    task_type: str = ""
    files_modified: list[str] = field(default_factory=list)
    diff_summary: str = ""
    error: str | None = None
    duration: float = 0.0
    action_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Code Agent
# --------------------------------------------------------------------------- #


class CodeAgent:
    """Agent that creates, modifies, and refactors source code.

    All file operations are performed through the AgentSandbox,
    ensuring path-traversal protection and full audit logging.

    Attributes:
        agent_id: Unique identifier for this agent instance.
        agent_role: Persona role (for permission lookups).
    """

    # Supported task types
    TASK_TYPES = {"create_file", "modify_file", "refactor", "delete_file"}

    def __init__(
        self,
        agent_id: str = "code-agent-01",
        agent_role: str = "developer",
    ) -> None:
        self.agent_id = agent_id
        self.agent_role = agent_role
        self._tasks_completed: list[TaskResult] = []

    def execute_task(
        self, task: dict[str, Any], sandbox: AgentSandbox,
    ) -> TaskResult:
        """Execute a code task within the sandbox.

        Args:
            task: Task specification with at minimum a 'type' field.
            sandbox: AgentSandbox for file operations.

        Returns:
            TaskResult with success/failure and details.
        """
        start = time.time()
        task_type = task.get("type", "")

        if task_type not in self.TASK_TYPES:
            return TaskResult(
                success=False,
                task_type=task_type,
                error=f"Unknown task type: '{task_type}'. "
                      f"Supported: {sorted(self.TASK_TYPES)}",
                duration=time.time() - start,
            )

        try:
            if task_type == "create_file":
                result = self._create_file(task, sandbox)
            elif task_type == "modify_file":
                result = self._modify_file(task, sandbox)
            elif task_type == "refactor":
                result = self._refactor(task, sandbox)
            elif task_type == "delete_file":
                result = self._delete_file(task, sandbox)
            else:
                result = TaskResult(
                    success=False,
                    task_type=task_type,
                    error=f"Unhandled task type: {task_type}",
                )

            result.duration = time.time() - start
            result.task_type = task_type
            self._tasks_completed.append(result)

            self._log_align("CODE_AGENT_TASK", {
                "agent_id": self.agent_id,
                "task_type": task_type,
                "success": result.success,
                "files_modified": result.files_modified,
                "duration": result.duration,
            })

            return result

        except Exception as e:
            result = TaskResult(
                success=False,
                task_type=task_type,
                error=f"Task execution failed: {e}",
                duration=time.time() - start,
            )
            self._tasks_completed.append(result)
            return result

    # ------------------------------------------------------------------ #
    # Task Implementations
    # ------------------------------------------------------------------ #

    def _create_file(
        self, task: dict[str, Any], sandbox: AgentSandbox,
    ) -> TaskResult:
        """Create a new file in the sandbox.

        Required task fields:
            path: Target file path (relative to worktree)
            content: File content to write

        Optional:
            description: Human-readable description of the file
        """
        path = task.get("path", "")
        content = task.get("content", "")

        if not path:
            return TaskResult(
                success=False,
                error="create_file requires 'path' field",
            )

        # Check if file already exists
        existing = sandbox.read_file(path)
        if existing.success:
            return TaskResult(
                success=False,
                error=f"File already exists: {path}. "
                      "Use 'modify_file' to update it.",
            )

        write_result = sandbox.write_file(path, content)
        if not write_result.success:
            return TaskResult(
                success=False,
                error=f"Failed to create {path}: {write_result.error}",
            )

        return TaskResult(
            success=True,
            files_modified=[path],
            diff_summary=f"Created {path} ({len(content)} bytes)",
            action_count=2,  # read check + write
            metadata={
                "bytes_written": len(content),
                "description": task.get("description", ""),
            },
        )

    def _modify_file(
        self, task: dict[str, Any], sandbox: AgentSandbox,
    ) -> TaskResult:
        """Modify an existing file in the sandbox.

        Required task fields:
            path: Target file path
            changes: List of {find: str, replace: str} dicts
              OR
            content: Full replacement content (overwrites entire file)
        """
        path = task.get("path", "")
        if not path:
            return TaskResult(
                success=False,
                error="modify_file requires 'path' field",
            )

        # Read existing content
        read_result = sandbox.read_file(path)
        if not read_result.success:
            return TaskResult(
                success=False,
                error=f"Cannot read {path}: {read_result.error}",
            )

        original = read_result.output

        # Apply changes
        if "content" in task:
            # Full replacement
            new_content = task["content"]
        elif "changes" in task:
            # Targeted find/replace
            new_content = original
            changes_applied = 0
            for change in task["changes"]:
                find = change.get("find", "")
                replace = change.get("replace", "")
                if find and find in new_content:
                    new_content = new_content.replace(find, replace, 1)
                    changes_applied += 1

            if changes_applied == 0:
                return TaskResult(
                    success=False,
                    error="No changes could be applied "
                          "(targets not found in file)",
                )
        else:
            return TaskResult(
                success=False,
                error="modify_file requires 'content' or 'changes' field",
            )

        # Write back
        write_result = sandbox.write_file(path, new_content)
        if not write_result.success:
            return TaskResult(
                success=False,
                error=f"Failed to write {path}: {write_result.error}",
            )

        # Generate diff summary
        added = len(new_content.splitlines()) - len(original.splitlines())
        sign = "+" if added >= 0 else ""
        diff_summary = (
            f"Modified {path}: "
            f"{len(original)} → {len(new_content)} bytes "
            f"({sign}{added} lines)"
        )

        return TaskResult(
            success=True,
            files_modified=[path],
            diff_summary=diff_summary,
            action_count=2,  # read + write
            metadata={
                "original_size": len(original),
                "new_size": len(new_content),
                "line_delta": added,
            },
        )

    def _refactor(
        self, task: dict[str, Any], sandbox: AgentSandbox,
    ) -> TaskResult:
        """Bulk find/replace across multiple files.

        Required task fields:
            find: Text to search for
            replace: Replacement text
            file_pattern: Glob pattern for files to process (default: "*.py")
        """
        find_text = task.get("find", "")
        replace_text = task.get("replace", "")
        file_pattern = task.get("file_pattern", "*.py")

        if not find_text:
            return TaskResult(
                success=False,
                error="refactor requires 'find' field",
            )

        # Search for matching files
        search_result = sandbox.search_files(find_text, file_pattern)
        if not search_result.success:
            return TaskResult(
                success=False,
                error=f"Search failed: {search_result.error}",
            )

        if not search_result.output:
            return TaskResult(
                success=True,
                diff_summary=f"No files contain '{find_text}'",
                action_count=1,
            )

        # Get unique files
        files_to_modify = list({m["file"] for m in search_result.output})
        modified_files: list[str] = []
        total_replacements = 0

        for filepath in files_to_modify:
            read_result = sandbox.read_file(filepath)
            if not read_result.success:
                continue

            original = read_result.output
            count = original.count(find_text)
            if count == 0:
                continue

            new_content = original.replace(find_text, replace_text)
            write_result = sandbox.write_file(filepath, new_content)
            if write_result.success:
                modified_files.append(filepath)
                total_replacements += count

        return TaskResult(
            success=True,
            files_modified=modified_files,
            diff_summary=(
                f"Refactored '{find_text}' → '{replace_text}' in "
                f"{len(modified_files)} files ({total_replacements} replacements)"
            ),
            action_count=1 + len(files_to_modify) * 2,
            metadata={
                "find": find_text,
                "replace": replace_text,
                "total_replacements": total_replacements,
            },
        )

    def _delete_file(
        self, task: dict[str, Any], sandbox: AgentSandbox,
    ) -> TaskResult:
        """Delete a file from the sandbox.

        Required task fields:
            path: File path to delete
        """
        path = task.get("path", "")
        if not path:
            return TaskResult(
                success=False,
                error="delete_file requires 'path' field",
            )

        result = sandbox.delete_file(path)
        if not result.success:
            return TaskResult(
                success=False,
                error=f"Failed to delete {path}: {result.error}",
            )

        return TaskResult(
            success=True,
            files_modified=[path],
            diff_summary=f"Deleted {path}",
            action_count=1,
        )

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    @property
    def tasks_completed(self) -> list[TaskResult]:
        """All tasks completed by this agent."""
        return list(self._tasks_completed)

    @property
    def task_count(self) -> int:
        """Number of tasks completed."""
        return len(self._tasks_completed)

    @property
    def success_rate(self) -> float:
        """Fraction of tasks that succeeded (0.0–1.0)."""
        if not self._tasks_completed:
            return 0.0
        successes = sum(1 for t in self._tasks_completed if t.success)
        return successes / len(self._tasks_completed)

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _log_align(self, event: str, data: dict) -> None:
        """Log to ALIGN ledger."""
        try:
            from agents.core.als_logger import ALSLogger
            als = ALSLogger()
            als.log(event, data)
        except ImportError:
            # ALIGN ledger is optional; skip logging if not installed
            logger.debug(
                "ALSLogger not available; skipping ALIGN logging for %s",
                event,
            )

