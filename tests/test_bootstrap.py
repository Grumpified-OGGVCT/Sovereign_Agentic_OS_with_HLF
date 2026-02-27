"""Tests for bootstrap stack healthchecks (mocked)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent


def test_docker_compose_exists() -> None:
    assert (REPO_ROOT / "docker-compose.yml").exists()


def test_dockerfile_base_exists() -> None:
    assert (REPO_ROOT / "Dockerfile.base").exists()


def test_bootstrap_script_exists() -> None:
    assert (REPO_ROOT / "bootstrap_all_in_one.sh").exists()


def test_docker_compose_no_sandbox_in_prod() -> None:
    """CI guard: OLLAMA_ALLOW_OPENCLAW=1 must not appear in docker-compose.yml."""
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    assert "OLLAMA_ALLOW_OPENCLAW=1" not in compose, (
        "OLLAMA_ALLOW_OPENCLAW=1 must not be present in production docker-compose.yml"
    )


def test_env_example_has_required_vars() -> None:
    env_example = (REPO_ROOT / ".env.example").read_text()
    required_vars = [
        "DEPLOYMENT_TIER",
        "OLLAMA_HOST",
        "REDIS_URL",
        "MAX_GAS_LIMIT",
        "VAULT_ADDR",
    ]
    for var in required_vars:
        assert var in env_example, f"Missing required env var: {var}"


def test_no_hardcoded_port_80_or_8080() -> None:
    """Validate no default ports 80/8080/5000 used (except Ollama 11434)."""
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    # Should not see :80: or :8080: or :5000: as port mappings
    forbidden_ports = ['"80:80"', '"8080:8080"', '"5000:5000"']
    for port in forbidden_ports:
        assert port not in compose, f"Forbidden default port found: {port}"
