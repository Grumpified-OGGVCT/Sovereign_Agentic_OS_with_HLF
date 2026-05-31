"""
tests/test_consolidation.py — Consolidator Gambit verification

Validates that the shared utility modules introduced in the Consolidator
Gambit (agents/core/ollama_config.py and agents/core/model_utils.py) behave
correctly and that their consumers (main.py, hat_engine.py, router.py) no
longer contain inline duplicates of the consolidated logic.

Tests are intentionally lightweight (no network / Redis / Ollama required).
"""

from __future__ import annotations

import importlib
import os

import pytest


# ---------------------------------------------------------------------------
# model_utils  — is_cloud_model / strip_cloud_suffix
# ---------------------------------------------------------------------------


class TestIsCloudModel:
    """Unit tests for agents.core.model_utils.is_cloud_model."""

    def setup_method(self) -> None:
        from agents.core.model_utils import is_cloud_model

        self.fn = is_cloud_model

    def test_bare_tag_cloud(self) -> None:
        assert self.fn("kimi-k2.5:cloud") is True

    def test_bare_tag_glm(self) -> None:
        assert self.fn("glm-5:cloud") is True

    def test_size_qualified_cloud(self) -> None:
        assert self.fn("qwen3-vl:32b-cloud") is True

    def test_size_qualified_gpt(self) -> None:
        assert self.fn("gpt-oss:120b-cloud") is True

    def test_local_model_not_cloud(self) -> None:
        assert self.fn("qwen:7b") is False

    def test_local_model_with_latest(self) -> None:
        assert self.fn("qwen:7b:latest") is False

    def test_empty_string(self) -> None:
        assert self.fn("") is False

    def test_model_containing_cloud_in_middle(self) -> None:
        # "cloud" in the middle of a name must NOT trigger detection
        assert self.fn("cloud-provider:7b") is False


class TestStripCloudSuffix:
    """Unit tests for agents.core.model_utils.strip_cloud_suffix."""

    def setup_method(self) -> None:
        from agents.core.model_utils import strip_cloud_suffix

        self.fn = strip_cloud_suffix

    def test_bare_tag_stripped(self) -> None:
        assert self.fn("kimi-k2.5:cloud") == "kimi-k2.5"

    def test_size_qualified_stripped(self) -> None:
        assert self.fn("qwen3-vl:32b-cloud") == "qwen3-vl:32b"

    def test_non_cloud_unchanged(self) -> None:
        assert self.fn("qwen:7b") == "qwen:7b"

    def test_empty_string(self) -> None:
        assert self.fn("") == ""

    def test_idempotent(self) -> None:
        bare = self.fn("kimi-k2.5:cloud")
        assert self.fn(bare) == bare


# ---------------------------------------------------------------------------
# ollama_config  — get_ollama_endpoints
# ---------------------------------------------------------------------------


class TestGetOllamaEndpoints:
    """Unit tests for agents.core.ollama_config.get_ollama_endpoints."""

    def _call(
        self,
        monkeypatch: pytest.MonkeyPatch,
        primary: str = "http://primary:11434",
        secondary: str = "",
        key: str = "",
        strategy: str = "failover",
    ) -> list:
        """Call get_ollama_endpoints with patched module attributes."""
        import agents.core.ollama_config as cfg

        monkeypatch.setattr(cfg, "_OLLAMA_PRIMARY", primary)
        monkeypatch.setattr(cfg, "_OLLAMA_SECONDARY", secondary)
        monkeypatch.setattr(cfg, "_OLLAMA_SECONDARY_KEY", key)
        monkeypatch.setattr(cfg, "_OLLAMA_STRATEGY", strategy)
        return cfg.get_ollama_endpoints()

    def test_primary_only_no_secondary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        eps = self._call(monkeypatch, secondary="")
        assert len(eps) == 1
        assert eps[0][0] == "http://primary:11434"

    def test_failover_returns_both(self, monkeypatch: pytest.MonkeyPatch) -> None:
        eps = self._call(monkeypatch, secondary="http://secondary:11434", strategy="failover")
        assert len(eps) == 2
        assert eps[0][0] == "http://primary:11434"
        assert eps[1][0] == "http://secondary:11434"

    def test_primary_only_strategy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        eps = self._call(monkeypatch, secondary="http://secondary:11434", strategy="primary_only")
        assert len(eps) == 1
        assert eps[0][0] == "http://primary:11434"

    def test_secondary_key_in_headers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        eps = self._call(monkeypatch, secondary="http://secondary:11434", key="tok123", strategy="failover")
        secondary_headers = eps[1][1]
        assert secondary_headers.get("Authorization") == "Bearer tok123"

    def test_primary_has_no_auth_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        eps = self._call(monkeypatch, secondary="http://secondary:11434", key="tok123", strategy="failover")
        primary_headers = eps[0][1]
        assert "Authorization" not in primary_headers


# ---------------------------------------------------------------------------
# Deduplication guard — ensure old inline code was removed
# ---------------------------------------------------------------------------


class TestDeduplicationGuards:
    """Ensure the consolidated logic no longer has inline duplicates in the
    consumer modules.  These checks read the source file directly so they
    are independent of import semantics."""

    def _read_source(self, relative_path: str) -> str:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, relative_path)) as fh:
            return fh.read()

    def test_main_py_no_inline_removesuffix_cloud(self) -> None:
        src = self._read_source("agents/core/main.py")
        assert 'removesuffix(":cloud")' not in src, (
            "agents/core/main.py should use strip_cloud_suffix() instead of inline removesuffix"
        )

    def test_main_py_imports_model_utils(self) -> None:
        src = self._read_source("agents/core/main.py")
        assert "from agents.core.model_utils import" in src

    def test_main_py_imports_ollama_config(self) -> None:
        src = self._read_source("agents/core/main.py")
        assert "from agents.core.ollama_config import" in src

    def test_hat_engine_imports_ollama_config(self) -> None:
        src = self._read_source("agents/core/hat_engine.py")
        assert "from agents.core.ollama_config import" in src

    def test_hat_engine_no_local_env_reads_for_ollama(self) -> None:
        src = self._read_source("agents/core/hat_engine.py")
        # The old inline reads should be gone
        assert 'os.environ.get("OLLAMA_HOST",' not in src

    def test_router_no_inline_is_cloud_definition(self) -> None:
        src = self._read_source("agents/gateway/router.py")
        # The standalone *top-level* (unindented) def should be gone.
        # The fallback definition inside the `except ImportError` block is
        # intentional and is indented, so it won't be matched here.
        top_level_defs = [
            line for line in src.splitlines()
            if line.startswith("def _is_cloud(")
        ]
        assert len(top_level_defs) == 0, (
            "router.py should no longer define _is_cloud() at module top-level; "
            "import it from agents.core.model_utils instead"
        )


# ---------------------------------------------------------------------------
# Smoke import: all three modules load cleanly
# ---------------------------------------------------------------------------


def test_model_utils_importable() -> None:
    mod = importlib.import_module("agents.core.model_utils")
    assert callable(mod.is_cloud_model)
    assert callable(mod.strip_cloud_suffix)


def test_ollama_config_importable() -> None:
    mod = importlib.import_module("agents.core.ollama_config")
    assert callable(mod.get_ollama_endpoints)
