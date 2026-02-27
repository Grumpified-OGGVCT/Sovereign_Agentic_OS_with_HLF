"""Shared pytest fixtures."""
from __future__ import annotations

import pytest
from pathlib import Path


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).parent.parent


@pytest.fixture
def hello_hlf(repo_root: Path) -> str:
    return (repo_root / "tests" / "fixtures" / "hello_world.hlf").read_text()
