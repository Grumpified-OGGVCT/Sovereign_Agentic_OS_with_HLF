"""
Tests for ADR (Architecture Decision Record) system.

Tests cover:
  - ADR creation and fields
  - Markdown rendering
  - JSON serialization
  - Registry CRUD
  - Status transitions
  - Supersession chain
  - Tagging and search
  - Persistence (save/load index)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from governance.adr import ADR, ADRStatus, ADRRegistry, _slugify


# ─── ADR Model ──────────────────────────────────────────────────────────────

class TestADR:
    def test_create(self) -> None:
        adr = ADR(id=1, title="Use Redis for bus", context="Need pub/sub")
        assert adr.status == ADRStatus.PROPOSED
        assert adr.title == "Use Redis for bus"

    def test_to_dict(self) -> None:
        adr = ADR(id=1, title="Test", tags=["infra"])
        d = adr.to_dict()
        assert d["id"] == 1
        assert "infra" in d["tags"]

    def test_from_dict(self) -> None:
        data = {"id": 2, "title": "X", "status": "accepted", "context": "ctx"}
        adr = ADR.from_dict(data)
        assert adr.status == ADRStatus.ACCEPTED

    def test_to_markdown(self) -> None:
        adr = ADR(
            id=1, title="Use Redis",
            context="Need streaming", decision="Use Redis Streams",
            consequences="Adds Redis dependency",
            tags=["infra", "messaging"],
        )
        md = adr.to_markdown()
        assert "# ADR-0001: Use Redis" in md
        assert "## Context" in md
        assert "## Decision" in md
        assert "Use Redis Streams" in md
        assert "infra, messaging" in md

    def test_markdown_supersession(self) -> None:
        adr = ADR(id=2, title="V2", supersedes=1)
        md = adr.to_markdown()
        assert "Supersedes" in md
        assert "0001" in md


# ─── Registry ────────────────────────────────────────────────────────────────

class TestRegistry:
    def test_create(self) -> None:
        reg = ADRRegistry()
        adr = reg.create("Use Redis", "Need pub/sub", "Redis Streams")
        assert adr.id == 1
        assert reg.count == 1

    def test_auto_increment(self) -> None:
        reg = ADRRegistry()
        a1 = reg.create("First", "ctx", "dec")
        a2 = reg.create("Second", "ctx", "dec")
        assert a2.id == a1.id + 1

    def test_get(self) -> None:
        reg = ADRRegistry()
        adr = reg.create("X", "c", "d")
        assert reg.get(adr.id) is adr
        assert reg.get(999) is None

    def test_update_status(self) -> None:
        reg = ADRRegistry()
        adr = reg.create("X", "c", "d")
        result = reg.update_status(adr.id, ADRStatus.ACCEPTED)
        assert result is not None
        assert result.status == ADRStatus.ACCEPTED

    def test_supersede(self) -> None:
        reg = ADRRegistry()
        old = reg.create("V1", "old ctx", "old dec")
        new = reg.create("V2", "new ctx", "new dec")
        old_result, new_result = reg.supersede(old.id, new)
        assert old_result is not None
        assert old_result.status == ADRStatus.SUPERSEDED
        assert old_result.superseded_by == new.id
        assert new_result.supersedes == old.id


# ─── Query ───────────────────────────────────────────────────────────────────

class TestQuery:
    def test_list_all(self) -> None:
        reg = ADRRegistry()
        reg.create("A", "c", "d")
        reg.create("B", "c", "d")
        assert len(reg.list_all()) == 2

    def test_by_status(self) -> None:
        reg = ADRRegistry()
        reg.create("A", "c", "d", status=ADRStatus.ACCEPTED)
        reg.create("B", "c", "d", status=ADRStatus.PROPOSED)
        assert len(reg.by_status(ADRStatus.ACCEPTED)) == 1

    def test_by_tag(self) -> None:
        reg = ADRRegistry()
        reg.create("A", "c", "d", tags=["security"])
        reg.create("B", "c", "d", tags=["infra"])
        assert len(reg.by_tag("security")) == 1

    def test_search(self) -> None:
        reg = ADRRegistry()
        reg.create("Redis Bus", "streaming context", "use redis")
        reg.create("SQLite Storage", "persistence", "use sqlite")
        results = reg.search("redis")
        assert len(results) == 1
        assert results[0].title == "Redis Bus"


# ─── Persistence ─────────────────────────────────────────────────────────────

class TestPersistence:
    def test_save_load_index(self, tmp_path: Path) -> None:
        reg = ADRRegistry()
        reg.create("X", "c", "d", tags=["test"])
        reg.create("Y", "c", "d")

        index_path = tmp_path / "index.json"
        reg.save_index(index_path)

        reg2 = ADRRegistry()
        loaded = reg2.load_index(index_path)
        assert loaded == 2
        assert reg2.get(1) is not None
        assert "test" in reg2.get(1).tags  # type: ignore

    def test_write_markdown_files(self, tmp_path: Path) -> None:
        reg = ADRRegistry(storage_dir=tmp_path / "decisions")
        reg.create("Use Redis", "ctx", "dec")
        files = list((tmp_path / "decisions").glob("ADR-*.md"))
        assert len(files) == 1
        assert "use-redis" in files[0].name.lower()

    def test_load_missing_file(self, tmp_path: Path) -> None:
        reg = ADRRegistry()
        loaded = reg.load_index(tmp_path / "nonexistent.json")
        assert loaded == 0


# ─── Helpers ─────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_slugify(self) -> None:
        assert _slugify("Use Redis for Bus Messaging") == "use-redis-for-bus-messaging"

    def test_slugify_special(self) -> None:
        assert "(" not in _slugify("Redis (v2)")
