"""
Clipboard — Cross-platform clipboard read/write.

Uses pyperclip when available, falls back to platform-specific
commands (pbcopy/xclip/clip.exe) via subprocess.
"""

from __future__ import annotations

import subprocess
import sys

from agents.core.native.bridge import ClipboardContent

# pyperclip is optional
try:
    import pyperclip

    _HAS_PYPERCLIP = True
except ImportError:
    pyperclip = None  # type: ignore[assignment]
    _HAS_PYPERCLIP = False


def clipboard_read() -> ClipboardContent:
    """Read current clipboard text content.

    Returns ClipboardContent with empty text if clipboard is empty
    or unavailable.
    """
    if _HAS_PYPERCLIP:
        try:
            text = pyperclip.paste() or ""
            return ClipboardContent(text=text)
        except Exception:
            pass

    # Platform fallback
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["powershell", "-Command", "Get-Clipboard"],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            return ClipboardContent(text=result.stdout.strip())
        elif sys.platform == "darwin":
            result = subprocess.run(
                ["pbpaste"],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            return ClipboardContent(text=result.stdout)
        else:
            # Linux — try xclip, then xsel, then wl-paste
            for cmd in [["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"], ["wl-paste"]]:
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=5.0)
                    if result.returncode == 0:
                        return ClipboardContent(text=result.stdout)
                except FileNotFoundError:
                    continue
    except Exception:
        pass

    return ClipboardContent(text="")


def clipboard_write(text: str) -> bool:
    """Write text to the system clipboard.

    Returns True on success, False on failure.
    """
    if _HAS_PYPERCLIP:
        try:
            pyperclip.copy(text)
            return True
        except Exception:
            pass

    # Platform fallback
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["powershell", "-Command", f"Set-Clipboard -Value '{text}'"],
                capture_output=True,
                timeout=5.0,
            )
            return True
        elif sys.platform == "darwin":
            proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            proc.communicate(text.encode("utf-8"))
            return proc.returncode == 0
        else:
            for cmd in [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"], ["wl-copy"]]:
                try:
                    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                    proc.communicate(text.encode("utf-8"))
                    if proc.returncode == 0:
                        return True
                except FileNotFoundError:
                    continue
    except Exception:
        pass

    return False
