"""Windows-only helpers: DPAPI, single-instance mutex, shell integration."""

from __future__ import annotations

import base64
import ctypes
import os
import subprocess
from pathlib import Path

ERROR_ALREADY_EXISTS = 183


def dpapi_protect(secret: str) -> str:
    """Encrypt a UTF-8 secret with the current user's DPAPI key, returning base64."""
    import win32crypt

    if secret == "":
        return ""
    blob = win32crypt.CryptProtectData(secret.encode("utf-8"), None, None, None, None, 0)
    return base64.b64encode(blob).decode("ascii")


def dpapi_unprotect(encoded: str) -> str:
    """Reverse :func:`dpapi_protect`."""
    import win32crypt

    raw = base64.b64decode(encoded.encode("ascii"))
    _description, data = win32crypt.CryptUnprotectData(raw, None, None, None, 0)
    return data.decode("utf-8")


class SingleInstance:
    """Named mutex that prevents two tray instances from running."""

    def __init__(self, name: str = "Switch2DiscordPresence") -> None:
        self._handle: int | None = None
        self._kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        self._handle = self._kernel32.CreateMutexW(None, False, f"Local\\{name}")
        self.already_running = self._kernel32.GetLastError() == ERROR_ALREADY_EXISTS

    def release(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> SingleInstance:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def open_in_explorer(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.startfile(str(path))  # type: ignore[attr-defined]


def reveal_file_command(path: Path) -> list[str]:
    return ["explorer", "/select,", str(path)]


def run_hidden(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
