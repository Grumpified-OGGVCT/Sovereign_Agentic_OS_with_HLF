"""
Tests for DiscordClient — REST operations for bots and webhooks.

Tests cover:
  - BotConfig / WebhookConfig / ChannelInfo data models
  - Webhook sending (mocked HTTP)
  - Build notifications
  - Bot operations (get user, list guilds, list channels)
  - Channel creation
  - Webhook creation
  - Vault integration
  - Error handling
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import patch, MagicMock

from agents.core.discord_client import (
    BotConfig,
    WebhookConfig,
    ChannelInfo,
    DiscordClient,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def bot() -> BotConfig:
    return BotConfig(token="test-token", guild_id="123456789", application_id="app-id")


@pytest.fixture
def webhook() -> WebhookConfig:
    return WebhookConfig(url="https://discord.com/api/webhooks/123/abc")


@pytest.fixture
def client(bot: BotConfig, webhook: WebhookConfig) -> DiscordClient:
    return DiscordClient(bot_config=bot, webhook_config=webhook)


@pytest.fixture
def webhook_only(webhook: WebhookConfig) -> DiscordClient:
    return DiscordClient(webhook_config=webhook)


@pytest.fixture
def no_config() -> DiscordClient:
    return DiscordClient()


# ─── Data Models ──────────────────────────────────────────────────────────────

class TestModels:
    def test_bot_headers(self, bot: BotConfig) -> None:
        h = bot.headers
        assert "Bot test-token" in h["Authorization"]

    def test_channel_from_api(self) -> None:
        data = {"id": "111", "name": "general", "type": 0, "guild_id": "222"}
        ch = ChannelInfo.from_api(data)
        assert ch.id == "111"
        assert ch.name == "general"
        assert ch.type == 0


# ─── Webhook ─────────────────────────────────────────────────────────────────

class TestWebhook:
    @patch("urllib.request.urlopen")
    def test_send_webhook(self, mock_open: MagicMock, client: DiscordClient) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"{}"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        result = client.send_webhook(content="Hello!")
        assert result["success"] is True

    def test_send_webhook_no_config(self, no_config: DiscordClient) -> None:
        result = no_config.send_webhook(content="fail")
        assert result["success"] is False

    @patch("urllib.request.urlopen")
    def test_build_notification(self, mock_open: MagicMock, client: DiscordClient) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"{}"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        result = client.send_build_notification(
            title="Build #42",
            description="All tests passing!",
            status="success",
            fields=[{"name": "Tests", "value": "1,631"}],
        )
        assert result["success"] is True


# ─── Bot Operations ──────────────────────────────────────────────────────────

class TestBotOps:
    @patch("urllib.request.urlopen")
    def test_get_bot_user(self, mock_open: MagicMock, client: DiscordClient) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"id": "123", "username": "SovBot"}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        result = client.get_bot_user()
        assert result["success"] is True
        assert result["data"]["username"] == "SovBot"

    def test_get_bot_no_config(self, no_config: DiscordClient) -> None:
        result = no_config.get_bot_user()
        assert result["success"] is False

    @patch("urllib.request.urlopen")
    def test_list_guilds(self, mock_open: MagicMock, client: DiscordClient) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps([{"id": "1", "name": "TestGuild"}]).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        result = client.list_guilds()
        assert result["success"] is True

    @patch("urllib.request.urlopen")
    def test_list_channels(self, mock_open: MagicMock, client: DiscordClient) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps([{"id": "1", "name": "general", "type": 0}]).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        result = client.list_channels()
        assert result["success"] is True

    def test_list_channels_no_guild(self) -> None:
        c = DiscordClient(bot_config=BotConfig(token="t"))
        result = c.list_channels()
        assert result["success"] is False

    @patch("urllib.request.urlopen")
    def test_send_message(self, mock_open: MagicMock, client: DiscordClient) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"id":"msg1"}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        result = client.send_message("ch-123", "Hello from build!")
        assert result["success"] is True

    @patch("urllib.request.urlopen")
    def test_create_channel(self, mock_open: MagicMock, client: DiscordClient) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"id": "new-ch", "name": "build-log"}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        result = client.create_channel("build-log", channel_type=0)
        assert result["success"] is True

    @patch("urllib.request.urlopen")
    def test_create_webhook(self, mock_open: MagicMock, client: DiscordClient) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"id": "wh1", "token": "abc"}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        result = client.create_webhook("ch-123", "Build Bot")
        assert result["success"] is True
        assert "webhook_url" in result


# ─── Vault Integration ───────────────────────────────────────────────────────

class TestVault:
    def test_from_vault(self) -> None:
        mock_vault = MagicMock()
        mock_vault.get_credential.return_value = {
            "api_key": "vault-token",
            "guild_id": "guild-999",
            "webhook_url": "https://discord.com/api/webhooks/x/y",
        }

        client = DiscordClient.from_vault(mock_vault)
        assert client._bot is not None
        assert client._bot.token == "vault-token"
        assert client._webhook is not None
        assert "webhooks/x/y" in client._webhook.url

    def test_from_vault_no_cred(self) -> None:
        mock_vault = MagicMock()
        mock_vault.get_credential.return_value = None

        client = DiscordClient.from_vault(mock_vault)
        assert client._bot is None
