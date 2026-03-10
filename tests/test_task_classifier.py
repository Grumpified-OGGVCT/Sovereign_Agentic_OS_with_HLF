"""
Tests for agents.core.task_classifier — full taxonomy, micro-task fast-path,
natural-language classification, launcher provenance, and vocabulary introspection.
"""

import pytest

from agents.core.task_classifier import (
    FAST_PATH_TYPES,
    TASK_TYPE_REGISTRY,
    TaskCategory,
    TaskEnvelope,
    TaskLauncher,
    TaskSize,
    classify_intent,
    classify_task,
    get_all_categories,
    get_task_types_for_category,
    get_vocabulary_summary,
)


# ─── Registry & Vocabulary ──────────────────────────────────────────────────


class TestRegistry:
    """Tests that the task type registry is well-formed."""

    def test_registry_has_50_types(self):
        assert len(TASK_TYPE_REGISTRY) == 50

    def test_all_entries_have_required_keys(self):
        required = {"category", "default_size", "gas", "agent"}
        for name, entry in TASK_TYPE_REGISTRY.items():
            assert required.issubset(entry.keys()), f"{name} missing keys"

    def test_all_categories_have_at_least_one_type(self):
        for cat in TaskCategory:
            types = get_task_types_for_category(cat)
            assert len(types) >= 1, f"Category {cat} has no types"

    def test_fast_path_types_are_micro(self):
        for t in FAST_PATH_TYPES:
            assert TASK_TYPE_REGISTRY[t]["default_size"] == TaskSize.MICRO

    def test_vocabulary_summary_structure(self):
        summary = get_vocabulary_summary()
        assert summary["total_types"] == 50
        assert summary["categories"] == len(TaskCategory)
        assert summary["fast_path_types"] == len(FAST_PATH_TYPES)
        assert "by_category" in summary
        assert len(summary["by_category"]) == len(TaskCategory)

    def test_get_all_categories(self):
        cats = get_all_categories()
        assert "code" in cats
        assert "browser" in cats
        assert "governance" in cats


# ─── Enumerations ────────────────────────────────────────────────────────────


class TestEnums:
    """Tests for TaskSize, TaskCategory, and TaskLauncher enums."""

    def test_task_size_values(self):
        assert list(TaskSize) == ["micro", "small", "medium", "large", "epic"]

    def test_task_category_count(self):
        assert len(TaskCategory) == 10

    def test_task_launcher_values(self):
        assert TaskLauncher.GATEWAY == "gateway"
        assert TaskLauncher.OPENCLAW == "openclaw"
        assert TaskLauncher.LOLLMS == "lollms"
        assert TaskLauncher.BROWSEROS == "browseros"
        assert TaskLauncher.JULES == "jules"
        assert TaskLauncher.CLI == "cli"
        assert TaskLauncher.HLF_RUNTIME == "hlf_runtime"
        assert TaskLauncher.MCP_CLIENT == "mcp_client"
        assert TaskLauncher.MANUAL == "manual"
        assert TaskLauncher.SCHEDULER == "scheduler"
        assert TaskLauncher.CANARY == "canary"

    def test_launcher_count(self):
        assert len(TaskLauncher) == 11


# ─── classify_task — Known Types ─────────────────────────────────────────────


class TestClassifyKnownTypes:
    """Tests for classifying registered task types."""

    def test_micro_edit_fast_path(self):
        env = classify_task({"type": "micro_edit", "path": "foo.py", "content": "x=1"})
        assert env.task_type == "micro_edit"
        assert env.size == TaskSize.MICRO
        assert env.fast_path is True
        assert env.category == TaskCategory.CODE
        assert env.agent_target == "code-agent"
        assert env.estimated_gas == 1
        assert env.confidence == 1.0

    def test_hotpatch_fast_path(self):
        env = classify_task({"type": "hotpatch"})
        assert env.fast_path is True
        assert env.size == TaskSize.MICRO

    def test_quick_fix_fast_path(self):
        env = classify_task({"type": "quick_fix"})
        assert env.fast_path is True

    def test_create_file_is_small(self):
        env = classify_task({"type": "create_file", "path": "new.py"})
        assert env.size == TaskSize.SMALL
        assert env.fast_path is False
        assert env.category == TaskCategory.CODE

    def test_refactor_is_medium(self):
        env = classify_task({"type": "refactor"})
        assert env.size == TaskSize.MEDIUM
        assert env.agent_target == "code-agent"

    def test_browser_navigate(self):
        env = classify_task({"type": "browser_navigate"})
        assert env.category == TaskCategory.BROWSER
        assert env.agent_target == "browser-agent"
        assert env.fast_path is True

    def test_deploy_prod_is_large(self):
        env = classify_task({"type": "deploy_prod"})
        assert env.size == TaskSize.LARGE
        assert env.category == TaskCategory.DEPLOY
        assert env.estimated_gas == 25

    def test_run_tests_build(self):
        env = classify_task({"type": "run_tests"})
        assert env.category == TaskCategory.BUILD
        assert env.agent_target == "build-agent"

    def test_align_check_governance(self):
        env = classify_task({"type": "align_check"})
        assert env.category == TaskCategory.GOVERNANCE
        assert env.fast_path is True

    def test_web_search_research(self):
        env = classify_task({"type": "web_search"})
        assert env.category == TaskCategory.RESEARCH
        assert env.agent_target == "research-agent"

    def test_update_readme_docs(self):
        env = classify_task({"type": "update_readme"})
        assert env.category == TaskCategory.DOCS

    def test_run_command_shell(self):
        env = classify_task({"type": "run_command"})
        assert env.category == TaskCategory.SHELL

    def test_mcp_invoke_api(self):
        env = classify_task({"type": "mcp_invoke"})
        assert env.category == TaskCategory.API

    def test_preflight_build(self):
        env = classify_task({"type": "preflight"})
        assert env.category == TaskCategory.BUILD
        assert env.size == TaskSize.MEDIUM
        assert env.estimated_gas == 10


# ─── classify_task — Size Estimation Heuristics ──────────────────────────────


class TestSizeEstimation:
    """Tests for content-driven size overrides."""

    def test_explicit_size_override(self):
        env = classify_task({"type": "create_file", "size": "epic"})
        assert env.size == TaskSize.EPIC

    def test_short_content_demotes_to_micro(self):
        env = classify_task({"type": "modify_file", "content": "x = 1\n"})
        assert env.size == TaskSize.MICRO

    def test_long_content_promotes_to_large(self):
        content = "\n".join([f"line_{i}" for i in range(500)])
        env = classify_task({"type": "create_file", "content": content})
        assert env.size == TaskSize.LARGE

    def test_huge_content_is_epic(self):
        content = "\n".join([f"line_{i}" for i in range(1500)])
        env = classify_task({"type": "create_file", "content": content})
        assert env.size == TaskSize.EPIC

    def test_single_change_is_micro(self):
        env = classify_task({
            "type": "modify_file",
            "changes": [{"find": "old", "replace": "new"}],
        })
        assert env.size == TaskSize.MICRO

    def test_many_changes_promotes(self):
        changes = [{"find": f"a{i}", "replace": f"b{i}"} for i in range(8)]
        env = classify_task({"type": "modify_file", "changes": changes})
        assert env.size in (TaskSize.MEDIUM, TaskSize.LARGE)

    def test_many_files_promotes_refactor(self):
        env = classify_task({
            "type": "refactor",
            "files": [f"f{i}.py" for i in range(10)],
        })
        assert env.size in (TaskSize.LARGE, TaskSize.EPIC)


# ─── Launcher Provenance ─────────────────────────────────────────────────────


class TestLauncherProvenance:
    """Tests that launcher is carried but NEVER alters behavior."""

    def test_default_launcher_is_manual(self):
        env = classify_task({"type": "micro_edit"})
        assert env.launcher == TaskLauncher.MANUAL

    def test_explicit_launcher_is_carried(self):
        env = classify_task({"type": "micro_edit"}, launcher=TaskLauncher.JULES)
        assert env.launcher == TaskLauncher.JULES

    def test_launcher_does_not_affect_routing(self):
        """CRITICAL: identical task must produce identical routing regardless of launcher."""
        task = {"type": "run_tests", "test_path": "tests/"}
        env_manual = classify_task(task, launcher=TaskLauncher.MANUAL)
        env_jules = classify_task(task, launcher=TaskLauncher.JULES)
        env_openclaw = classify_task(task, launcher=TaskLauncher.OPENCLAW)
        env_lollms = classify_task(task, launcher=TaskLauncher.LOLLMS)
        env_browseros = classify_task(task, launcher=TaskLauncher.BROWSEROS)

        # Same task → same routing for ALL launchers
        assert env_manual.agent_target == env_jules.agent_target == env_openclaw.agent_target
        assert env_manual.category == env_lollms.category == env_browseros.category
        assert env_manual.estimated_gas == env_jules.estimated_gas
        assert env_manual.fast_path == env_openclaw.fast_path
        assert env_manual.size == env_lollms.size

    def test_launcher_in_to_dict(self):
        env = classify_task({"type": "micro_edit"}, launcher=TaskLauncher.GATEWAY)
        d = env.to_dict()
        assert d["launcher"] == "gateway"

    def test_all_launchers_accepted(self):
        """Every registered launcher must classify without error."""
        for launcher in TaskLauncher:
            env = classify_task({"type": "check_syntax"}, launcher=launcher)
            assert env.launcher == launcher


# ─── classify_intent — Natural Language ──────────────────────────────────────


class TestClassifyIntent:
    """Tests for NL intent classification."""

    def test_fix_intent(self):
        env = classify_intent("fix the off-by-one error in parser.py")
        assert env.task_type == "quick_fix"
        assert env.fast_path is True

    def test_rename_intent(self):
        env = classify_intent("rename process_data to transform_data")
        assert env.task_type == "rename_symbol"

    def test_deploy_staging_intent(self):
        env = classify_intent("deploy to staging environment")
        assert env.task_type == "deploy_staging"
        assert env.category == TaskCategory.DEPLOY

    def test_run_tests_intent(self):
        env = classify_intent("run the unit tests for the auth module")
        assert env.task_type == "run_tests"

    def test_navigate_intent(self):
        env = classify_intent("navigate to the url https://example.com")
        assert env.category == TaskCategory.BROWSER

    def test_create_pr_intent(self):
        env = classify_intent("create a pull request for this branch")
        assert env.task_type == "create_pr"

    def test_update_docs_intent(self):
        env = classify_intent("update the readme with the new API endpoints")
        assert env.category == TaskCategory.DOCS

    def test_lint_intent(self):
        env = classify_intent("run ruff on the codebase")
        assert env.task_type == "run_lint"

    def test_unknown_intent_fallback(self):
        env = classify_intent("do something completely unusual and unprecedented")
        assert env.confidence < 0.5
        assert env.task_type == "unknown"

    def test_intent_launcher_carried(self):
        env = classify_intent("fix the bug", launcher=TaskLauncher.LOLLMS)
        assert env.launcher == TaskLauncher.LOLLMS


# ─── Heuristic Classification (Unknown Types) ───────────────────────────────


class TestHeuristicClassification:
    """Tests for unknown task types classified by content heuristics."""

    def test_path_plus_content_is_code(self):
        env = classify_task({"type": "custom_write", "path": "x.py", "content": "y=1"})
        assert env.category == TaskCategory.CODE
        assert env.confidence == 0.5

    def test_path_without_content_is_build(self):
        env = classify_task({"type": "custom_check", "path": "x.py"})
        assert env.category == TaskCategory.BUILD

    def test_command_is_shell(self):
        env = classify_task({"type": "custom_cmd", "command": "ls -la"})
        assert env.category == TaskCategory.SHELL

    def test_url_is_browser(self):
        env = classify_task({"type": "custom_browse", "url": "https://example.com"})
        assert env.category == TaskCategory.BROWSER

    def test_empty_task_is_code_fallback(self):
        env = classify_task({"type": "unknown_thing"})
        assert env.category == TaskCategory.CODE
        assert env.confidence == 0.5


# ─── TaskEnvelope ────────────────────────────────────────────────────────────


class TestTaskEnvelope:
    """Tests for the TaskEnvelope dataclass."""

    def test_to_dict_complete(self):
        env = TaskEnvelope(
            task={"type": "micro_edit"},
            task_type="micro_edit",
            category=TaskCategory.CODE,
            size=TaskSize.MICRO,
            estimated_gas=1,
            agent_target="code-agent",
            launcher=TaskLauncher.JULES,
            fast_path=True,
            confidence=0.95,
            reasoning="test",
        )
        d = env.to_dict()
        assert d["task_type"] == "micro_edit"
        assert d["category"] == "code"
        assert d["size"] == "micro"
        assert d["estimated_gas"] == 1
        assert d["agent_target"] == "code-agent"
        assert d["launcher"] == "jules"
        assert d["fast_path"] is True
        assert d["confidence"] == 0.95

    def test_default_launcher(self):
        env = TaskEnvelope(
            task={}, task_type="x", category=TaskCategory.CODE,
            size=TaskSize.SMALL, estimated_gas=1, agent_target="a",
        )
        assert env.launcher == TaskLauncher.MANUAL
