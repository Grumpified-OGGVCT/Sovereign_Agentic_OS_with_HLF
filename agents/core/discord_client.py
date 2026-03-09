"""
Discord Operations Client — Manages Discord bots, webhooks, and channels.

Provides programmatic operations for the Sovereign OS build pipeline:
    - Create and manage Discord bots/applications
    - Setup webhooks for build notifications
    - Manage channels and permissions
    - Send messages and embeds
    - OAuth2 bot token management

This is NOT an MCP server itself — it's a client module that HLF host
functions and agents can call to interact with the Discord API.

Uses discord.py REST API only (no gateway/websocket for simplicity).
"""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DISCORD_API_BASE = "https://discord.com/api/v10"


# ─── Data Models ─────────────────────────────────────────────────────────────

@dataclass
class BotConfig:
    """Discord bot configuration."""

    token: str = ""
    application_id: str = ""
    guild_id: str = ""
    name: str = ""

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bot {self.token}",
            "Content-Type": "application/json",
        }


@dataclass
class WebhookConfig:
    """Discord webhook configuration."""

    url: str = ""
    name: str = "Sovereign OS"
    avatar_url: str = ""


@dataclass
class ChannelInfo:
    """Discord channel metadata."""

    id: str
    name: str
    type: int  # 0=text, 2=voice, 4=category
    guild_id: str = ""
    parent_id: str = ""

    @classmethod
    def from_api(cls, data: dict) -> ChannelInfo:
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            type=data.get("type", 0),
            guild_id=data.get("guild_id", ""),
            parent_id=data.get("parent_id", ""),
        )


# ─── Discord Client ─────────────────────────────────────────────────────────

class DiscordClient:
    """Discord REST API client for bot and webhook operations.

    Supports:
        - Webhook message sending (no bot token required)
        - Channel management (requires bot token)
        - Bot user info
        - Guild inspection
        - Build notification pipelines

    Args:
        bot_config: Optional bot config for full API access.
        webhook_config: Optional webhook for simple notifications.
    """

    def __init__(
        self,
        bot_config: BotConfig | None = None,
        webhook_config: WebhookConfig | None = None,
    ) -> None:
        self._bot = bot_config
        self._webhook = webhook_config

    # ── Webhook Operations ──────────────────────────────────────────────

    def send_webhook(
        self,
        content: str = "",
        embeds: list[dict] | None = None,
        username: str = "",
    ) -> dict[str, Any]:
        """Send a message via webhook (no bot token needed).

        Args:
            content: Text content.
            embeds: List of Discord embed objects.
            username: Override webhook username.

        Returns:
            Dict with 'success' and optional 'error'.
        """
        if not self._webhook or not self._webhook.url:
            return {"success": False, "error": "No webhook configured"}

        payload: dict[str, Any] = {}
        if content:
            payload["content"] = content
        if embeds:
            payload["embeds"] = embeds
        if username:
            payload["username"] = username

        return self._post(self._webhook.url, payload)

    def send_build_notification(
        self,
        title: str,
        description: str,
        status: str = "success",
        fields: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Send a build/test notification as an embed.

        Args:
            title: Notification title.
            description: Notification body.
            status: "success", "failure", or "info".
            fields: Optional embed fields [{name, value, inline}].
        """
        colors = {
            "success": 0x6CBC61,   # Sovereign green
            "failure": 0xE74C3C,   # Red
            "info": 0x3498DB,      # Blue
            "warning": 0xF39C12,   # Orange
        }

        embed: dict[str, Any] = {
            "title": title,
            "description": description,
            "color": colors.get(status, 0x95A5A6),
            "footer": {"text": "Sovereign OS Build System"},
        }

        if fields:
            embed["fields"] = [
                {"name": f["name"], "value": f["value"], "inline": f.get("inline", True)}
                for f in fields
            ]

        return self.send_webhook(embeds=[embed])

    # ── Bot Operations ──────────────────────────────────────────────────

    def get_bot_user(self) -> dict[str, Any]:
        """Get the authenticated bot's user info."""
        if not self._bot:
            return {"success": False, "error": "No bot configured"}

        return self._get("/users/@me")

    def list_guilds(self) -> dict[str, Any]:
        """List guilds the bot is a member of."""
        if not self._bot:
            return {"success": False, "error": "No bot configured"}

        return self._get("/users/@me/guilds")

    def list_channels(self, guild_id: str = "") -> dict[str, Any]:
        """List channels in a guild.

        Args:
            guild_id: Guild ID (uses bot_config.guild_id if empty).
        """
        if not self._bot:
            return {"success": False, "error": "No bot configured"}

        gid = guild_id or self._bot.guild_id
        if not gid:
            return {"success": False, "error": "No guild_id specified"}

        return self._get(f"/guilds/{gid}/channels")

    def create_channel(
        self,
        name: str,
        channel_type: int = 0,
        parent_id: str = "",
        guild_id: str = "",
    ) -> dict[str, Any]:
        """Create a channel in a guild.

        Args:
            name: Channel name.
            channel_type: 0=text, 2=voice, 4=category.
            parent_id: Category channel ID.
            guild_id: Guild ID (uses bot_config.guild_id if empty).
        """
        if not self._bot:
            return {"success": False, "error": "No bot configured"}

        gid = guild_id or self._bot.guild_id
        if not gid:
            return {"success": False, "error": "No guild_id specified"}

        payload: dict[str, Any] = {"name": name, "type": channel_type}
        if parent_id:
            payload["parent_id"] = parent_id

        return self._post(f"{DISCORD_API_BASE}/guilds/{gid}/channels", payload, use_bot_auth=True)

    def send_message(
        self,
        channel_id: str,
        content: str,
        embeds: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Send a message to a channel.

        Args:
            channel_id: Target channel ID.
            content: Message text.
            embeds: Optional embeds.
        """
        if not self._bot:
            return {"success": False, "error": "No bot configured"}

        payload: dict[str, Any] = {}
        if content:
            payload["content"] = content
        if embeds:
            payload["embeds"] = embeds

        return self._post(
            f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
            payload,
            use_bot_auth=True,
        )

    def create_webhook(
        self,
        channel_id: str,
        name: str = "Sovereign OS",
    ) -> dict[str, Any]:
        """Create a webhook in a channel.

        Args:
            channel_id: Channel to create webhook in.
            name: Webhook display name.

        Returns:
            Dict with webhook URL on success.
        """
        if not self._bot:
            return {"success": False, "error": "No bot configured"}

        payload = {"name": name}
        result = self._post(
            f"{DISCORD_API_BASE}/channels/{channel_id}/webhooks",
            payload,
            use_bot_auth=True,
        )

        if result.get("success") and "id" in result.get("data", {}):
            data = result["data"]
            result["webhook_url"] = f"https://discord.com/api/webhooks/{data['id']}/{data.get('token', '')}"

        return result

    # ── Vault Integration ───────────────────────────────────────────────

    @classmethod
    def from_vault(cls, vault: Any) -> DiscordClient:
        """Create a client from credential vault.

        Looks for 'discord' provider with token and guild_id.
        """
        try:
            cred = vault.get_credential("discord")
            if cred:
                bot = BotConfig(
                    token=cred.get("api_key", ""),
                    guild_id=cred.get("guild_id", ""),
                    application_id=cred.get("application_id", ""),
                )
                webhook_url = cred.get("webhook_url", "")
                webhook = WebhookConfig(url=webhook_url) if webhook_url else None
                return cls(bot_config=bot, webhook_config=webhook)
        except Exception:
            pass

        return cls()

    # ── Internal HTTP ───────────────────────────────────────────────────

    def _get(self, endpoint: str) -> dict[str, Any]:
        """GET request to Discord API."""
        if not self._bot:
            return {"success": False, "error": "No bot configured"}

        url = f"{DISCORD_API_BASE}{endpoint}" if not endpoint.startswith("http") else endpoint

        try:
            req = urllib.request.Request(url, headers=self._bot.headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                return {"success": True, "data": data}
        except urllib.error.HTTPError as e:
            return {"success": False, "error": f"HTTP {e.code}", "detail": e.read().decode()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _post(
        self,
        url: str,
        payload: dict[str, Any],
        use_bot_auth: bool = False,
    ) -> dict[str, Any]:
        """POST request to Discord API or webhook URL."""
        headers = {"Content-Type": "application/json"}
        if use_bot_auth and self._bot:
            headers.update(self._bot.headers)

        try:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode()
                resp_data = json.loads(body) if body else {}
                return {"success": True, "data": resp_data}
        except urllib.error.HTTPError as e:
            return {"success": False, "error": f"HTTP {e.code}", "detail": e.read().decode()}
        except Exception as e:
            return {"success": False, "error": str(e)}
