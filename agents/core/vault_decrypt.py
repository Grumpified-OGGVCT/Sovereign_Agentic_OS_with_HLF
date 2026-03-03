"""
Vault decrypt — retrieves secrets from HashiCorp Vault via AppRole auth.
Uses pydantic.BaseSettings — never hardcodes secrets.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic_settings import BaseSettings


class VaultSettings(BaseSettings):
    vault_addr: str = "http://vault:8200"
    vault_role_id: str = ""
    vault_secret_id: str = ""

    model_config = {"env_file": ".env"}


_settings = VaultSettings()


def _authenticate() -> str:
    """Obtain Vault token via AppRole login."""
    resp = httpx.post(
        f"{_settings.vault_addr}/v1/auth/approle/login",
        json={"role_id": _settings.vault_role_id, "secret_id": _settings.vault_secret_id},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["auth"]["client_token"]


def read_secret(path: str) -> dict[str, Any]:
    """Read a KV v2 secret from Vault."""
    token = _authenticate()
    resp = httpx.get(
        f"{_settings.vault_addr}/v1/secret/data/{path}",
        headers={"X-Vault-Token": token},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("data", {}).get("data", {})
