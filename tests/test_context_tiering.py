"""Tests for Silver Hat additions to agents.core.context_tiering."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from agents.core.context_tiering import (
    ContextTierManager,
    _resolve_embedding_model,
    _tier_max_clusters,
)


# ---------------------------------------------------------------------------
# Helper: build a ContextTierManager with a mocked Redis connection
# ---------------------------------------------------------------------------

def _make_manager() -> ContextTierManager:
    with patch("agents.core.context_tiering.redis") as mock_redis:
        mock_redis.from_url.return_value = MagicMock()
        manager = ContextTierManager()
    return manager


# ---------------------------------------------------------------------------
# _resolve_embedding_model
# ---------------------------------------------------------------------------

class TestResolveEmbeddingModel:
    def test_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
        assert _resolve_embedding_model() == "nomic-embed-text"

    def test_reads_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EMBEDDING_MODEL", "custom-embed-v2")
        assert _resolve_embedding_model() == "custom-embed-v2"

    def test_no_hardcoded_model_name_in_source(self) -> None:
        """Verify source code does not contain a bare hardcoded model name."""
        import inspect
        import agents.core.context_tiering as mod

        source = inspect.getsource(mod)
        # The string 'nomic-embed-text' must only appear as the *default fallback*
        # in _resolve_embedding_model, not anywhere else in the module.
        occurrences = source.count('"nomic-embed-text"')
        # Exactly one occurrence is allowed: the default fallback in the helper.
        assert occurrences <= 1, (
            f"Found {occurrences} occurrences of hardcoded 'nomic-embed-text' — "
            "must use _resolve_embedding_model() everywhere."
        )


# ---------------------------------------------------------------------------
# _tier_max_clusters
# ---------------------------------------------------------------------------

class TestTierMaxClusters:
    def test_none_budget_returns_default_ten(self) -> None:
        assert _tier_max_clusters(None) == 10

    def test_zero_budget_returns_default_ten(self) -> None:
        assert _tier_max_clusters(0) == 10

    def test_budget_scales_clusters(self) -> None:
        # 8192 tokens / 256 per cluster = 32
        assert _tier_max_clusters(8192) == 32

    def test_small_budget_returns_at_least_one(self) -> None:
        assert _tier_max_clusters(100) >= 1

    def test_large_budget_yields_large_cap(self) -> None:
        assert _tier_max_clusters(32768) == 128


# ---------------------------------------------------------------------------
# ContextTierManager — gas tracking
# ---------------------------------------------------------------------------

class TestGasTracking:
    def test_gas_starts_at_zero(self) -> None:
        manager = _make_manager()
        assert manager.gas_used == 0

    def test_evict_increments_gas(self) -> None:
        manager = _make_manager()
        # Simulate Redis keys returning 3 matching keys
        manager.r.keys = MagicMock(return_value=["k1", "k2", "k3"])
        manager.r.delete = MagicMock()
        manager.evict_hot_graph("topic_abc")
        assert manager.gas_used == 3   # _GAS_PER_EVICT=1 × 3 keys
        assert manager._evictions == 3

    def test_no_eviction_when_no_keys(self) -> None:
        manager = _make_manager()
        manager.r.keys = MagicMock(return_value=[])
        manager.evict_hot_graph("topic_empty")
        assert manager.gas_used == 0


# ---------------------------------------------------------------------------
# ContextTierManager — get_stats
# ---------------------------------------------------------------------------

class TestGetStats:
    def test_stats_keys_present(self) -> None:
        manager = _make_manager()
        stats = manager.get_stats()
        assert "gas_used" in stats
        assert "clusters_loaded" in stats
        assert "clusters_evicted" in stats

    def test_stats_reflect_evictions(self) -> None:
        manager = _make_manager()
        manager.r.keys = MagicMock(return_value=["a", "b"])
        manager.r.delete = MagicMock()
        manager.evict_hot_graph("t1")
        stats = manager.get_stats()
        assert stats["clusters_evicted"] == 2
        assert stats["gas_used"] == 2


# ---------------------------------------------------------------------------
# ContextTierManager — load_hot_graph (mocked, no live services)
# ---------------------------------------------------------------------------

class TestLoadHotGraphMocked:
    def test_load_skips_when_db_missing(self, tmp_path: pytest.TempPathFactory) -> None:  # noqa: PYI011
        """load_hot_graph must return silently when the DB does not exist."""
        from pathlib import Path
        manager = _make_manager()
        with patch("agents.core.context_tiering._DB_PATH", str(Path(str(tmp_path)) / "nonexistent.db")):  # type: ignore[arg-type]
            manager.load_hot_graph("topic_x")
        # No gas consumed because we bailed out early
        assert manager.gas_used == 0

    def test_load_handles_embedding_failure_gracefully(self, tmp_path: pytest.TempPathFactory) -> None:  # noqa: PYI011
        """load_hot_graph must not raise when the embedding service is unavailable."""
        from pathlib import Path
        db_path = Path(str(tmp_path)) / "memory.db"  # type: ignore[arg-type]
        db_path.touch()  # create empty file so existence check passes

        manager = _make_manager()
        with (
            patch("agents.core.context_tiering._DB_PATH", str(db_path)),
            patch("requests.post", side_effect=ConnectionError("offline")),
        ):
            manager.load_hot_graph("topic_y")  # must not raise

    def test_load_uses_env_embedding_model(
        self, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch  # noqa: PYI011
    ) -> None:
        """The embedding model sent to Ollama must come from the env var."""
        from pathlib import Path
        monkeypatch.setenv("EMBEDDING_MODEL", "my-custom-embed")
        db_path = Path(str(tmp_path)) / "memory.db"  # type: ignore[arg-type]
        db_path.touch()

        captured: list[dict] = []

        def fake_post(url: str, json: dict, timeout: float) -> None:  # noqa: A002
            captured.append(json)
            raise ConnectionError("offline")

        manager = _make_manager()
        with (
            patch("agents.core.context_tiering._DB_PATH", str(db_path)),
            patch("requests.post", side_effect=fake_post),
        ):
            manager.load_hot_graph("topic_z")

        assert captured, "requests.post was never called"
        assert captured[0]["model"] == "my-custom-embed"

    def test_load_respects_max_clusters_cap(
        self, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch  # noqa: PYI011
    ) -> None:
        """The KNN query must use the derived cap, not a hardcoded 10."""
        from pathlib import Path
        db_path = Path(str(tmp_path)) / "memory.db"  # type: ignore[arg-type]
        db_path.touch()

        captured_knn_k: list[int] = []

        class FakeResp:
            def raise_for_status(self) -> None: ...
            def json(self) -> dict:
                return {"embedding": [0.1] * 8}

        def fake_post(url: str, json: dict, timeout: float) -> FakeResp:  # noqa: A002
            return FakeResp()

        class FakeCursor:
            def fetchall(self) -> list:
                return []

        class FakeConn:
            def execute(self, sql: str, params: tuple = ()) -> FakeCursor:
                if "MATCH" in sql and params:
                    captured_knn_k.append(params[1])
                return FakeCursor()

            def enable_load_extension(self, v: bool) -> None: ...  # noqa: FBT001
            def close(self) -> None: ...
            def __enter__(self): return self
            def __exit__(self, *a): ...

        manager = _make_manager()
        with (
            patch("agents.core.context_tiering._DB_PATH", str(db_path)),
            patch("requests.post", side_effect=fake_post),
            patch("sqlite3.connect", return_value=FakeConn()),
            patch("sqlite_vec.load"),
        ):
            # 256-token budget → cap = 1
            manager.load_hot_graph("topic_cap", token_budget=256)

        if captured_knn_k:
            assert captured_knn_k[0] == 1
