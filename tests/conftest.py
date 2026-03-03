"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).parent.parent


@pytest.fixture
def hello_hlf(repo_root: Path) -> str:
    return (repo_root / "tests" / "fixtures" / "hello_world.hlf").read_text(encoding="utf-8")
