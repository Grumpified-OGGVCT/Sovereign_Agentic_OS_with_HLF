"""
Notifications — Cross-platform desktop notifications.

Uses desktop-notifier when available, falls back to platform-specific
tools (PowerShell toast, osascript, notify-send).
"""

from __future__ import annotations

import subprocess
import sys

from agents.core.native.bridge import NotificationRequest

# desktop-notifier is optional
_HAS_DESKTOP_NOTIFIER = False
try:
    from desktop_notifier import DesktopNotifier, Urgency

    _HAS_DESKTOP_NOTIFIER = True
except ImportError:
    pass


async def _send_via_desktop_notifier(request: NotificationRequest) -> bool:
    """Send notification via desktop-notifier library (async)."""
    notifier = DesktopNotifier(app_name="Sovereign OS")

    urgency_map = {
        "low": Urgency.Low,
        "normal": Urgency.Normal,
        "critical": Urgency.Critical,
    }

    await notifier.send(
        title=request.title,
        message=request.body,
        urgency=urgency_map.get(request.urgency.value, Urgency.Normal),
    )
    return True


def notify(request: NotificationRequest) -> bool:
    """Send a desktop notification.

    Tries desktop-notifier first, then platform-specific fallbacks.
    Returns True on success, False on failure.
    """
    if _HAS_DESKTOP_NOTIFIER:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule in running loop
                asyncio.ensure_future(_send_via_desktop_notifier(request))
                return True
            else:
                return loop.run_until_complete(_send_via_desktop_notifier(request))
        except RuntimeError:
            return asyncio.run(_send_via_desktop_notifier(request))

    # Platform fallback
    return _notify_fallback(request)


def _notify_fallback(request: NotificationRequest) -> bool:
    """Platform-specific notification fallback."""
    try:
        if sys.platform == "win32":
            # PowerShell BurntToast or basic toast
            ps_script = (
                f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, '
                f'ContentType = WindowsRuntime] > $null; '
                f'$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(0); '
                f'$text = $template.GetElementsByTagName("text"); '
                f'$text[0].AppendChild($template.CreateTextNode("{request.title}")) > $null; '
                f'$text[1].AppendChild($template.CreateTextNode("{request.body}")) > $null; '
                f'$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Sovereign OS"); '
                f'$notifier.Show([Windows.UI.Notifications.ToastNotification]::new($template))'
            )
            subprocess.run(
                ["powershell", "-Command", ps_script],
                capture_output=True,
                timeout=10.0,
            )
            return True

        elif sys.platform == "darwin":
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{request.body}" with title "{request.title}"',
                ],
                capture_output=True,
                timeout=5.0,
            )
            return True

        else:
            # Linux — notify-send
            cmd = ["notify-send", request.title, request.body]
            urgency_map = {"low": "low", "normal": "normal", "critical": "critical"}
            cmd.extend(["-u", urgency_map.get(request.urgency.value, "normal")])
            if request.timeout_ms > 0:
                cmd.extend(["-t", str(request.timeout_ms)])
            subprocess.run(cmd, capture_output=True, timeout=5.0)
            return True

    except Exception:
        return False
