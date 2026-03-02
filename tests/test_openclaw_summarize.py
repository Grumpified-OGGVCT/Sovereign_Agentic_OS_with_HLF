"""Tests for OPENCLAW_SUMMARIZE entry in host_functions.json."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture
def host_functions() -> list[dict]:
    path = REPO_ROOT / "governance" / "host_functions.json"
    assert path.exists(), "governance/host_functions.json missing"
    with path.open() as f:
        data = json.load(f)
    return data["functions"]


def test_openclaw_summarize_exists(host_functions) -> None:
    names = [f["name"] for f in host_functions]
    assert "OPENCLAW_SUMMARIZE" in names, "OPENCLAW_SUMMARIZE not found in host_functions.json"


def test_openclaw_gas_in_range(host_functions) -> None:
    fn = next(f for f in host_functions if f["name"] == "OPENCLAW_SUMMARIZE")
    assert 5 <= fn["gas"] <= 10, f"OPENCLAW_SUMMARIZE gas {fn['gas']} not in range [5, 10]"


def test_openclaw_sensitive_true(host_functions) -> None:
    fn = next(f for f in host_functions if f["name"] == "OPENCLAW_SUMMARIZE")
    assert fn["sensitive"] is True, "OPENCLAW_SUMMARIZE must have sensitive=true"


def test_openclaw_binary_sha256_field_exists(host_functions) -> None:
    fn = next(f for f in host_functions if f["name"] == "OPENCLAW_SUMMARIZE")
    assert "binary_sha256" in fn, "OPENCLAW_SUMMARIZE must have binary_sha256 field"


def test_openclaw_tier_forge_or_sovereign(host_functions) -> None:
    fn = next(f for f in host_functions if f["name"] == "OPENCLAW_SUMMARIZE")
    tiers = fn.get("tier", [])
    assert "forge" in tiers or "sovereign" in tiers, (
        "OPENCLAW_SUMMARIZE must be available on forge or sovereign tier"
    )


def test_host_functions_schema(host_functions) -> None:
    required_fields = ["name", "args", "returns", "tier", "gas", "backend", "sensitive"]
    for fn in host_functions:
        for field in required_fields:
            assert field in fn, f"Function {fn.get('name', '?')} missing field: {field}"
