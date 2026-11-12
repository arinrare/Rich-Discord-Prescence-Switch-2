"""System tray UI (pystray)."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageDraw

from .config import logs_dir

ICON_FILES = {
    "off": "tray_off.png",
    "home": "tray_home.png",
    "game": "tray_game.png",
    "handheld": "tray_handheld.png",
    "error": "tray_error.png",
}
ICON_COLORS = {
    "off": (110, 110, 118),
    "home": (64, 156, 255),
    "game": (76, 187, 108),
    "handheld": (176, 122, 255),
    "error": (230, 74, 74),
}


@dataclass
class TrayStatus:
    state: str = "OFF"
    description: str = "Starting…"
    game: str | None = None
    paused: bool = False
    handheld: bool = False
    obs_connected: bool = False
    discord_connected: bool = False
    discord_enabled: bool = False
    last_game: str | None = None
    games: tuple[str, ...] = field(default_factory=tuple)
    error: str | None = None


class CommandSink(Protocol):
    def __call__(self, name: str, *args: object) -> None: ...


def _assets_dir() -> Path:
    candidates = [
        Path(getattr(sys, "_MEIPASS", "")) / "assets" if getattr(sys, "_MEIPASS", None) else None,
        Path(__file__).resolve().parents[2] / "assets",
        Path.cwd() / "assets",
    ]
    for candidate in candidates:
        if candidate and candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parents[2] / "assets"


def _fallback_icon(kind: str, size: int = 64) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    color = ICON_COLORS.get(kind, ICON_COLORS["off"])
    draw.rounded_rectangle((4, 4, size - 4, size - 4), radius=14, fill=(32, 34, 40, 255), outline=color, width=3)
    draw.rounded_rectangle((14, 18, 28, size - 18), radius=6, fill=(0, 196, 220, 255))
    draw.rounded_rectangle((size - 28, 18, size - 14, size - 18), radius=6, fill=(230, 70, 80, 255))
    draw.ellipse((size // 2 - 4, size // 2 - 4, size // 2 + 4, size // 2 + 4), fill=color)
    return image


def load_icon(kind: str) -> Image.Image:
    name = ICON_FILES.get(kind, ICON_FILES["off"])
    path = _assets_dir() / name
    if path.exists():
        try:
            return Image.open(path).convert("RGBA")
        except Exception:
            pass
    return _fallback_icon(kind)


class Tray:
    def __init__(self, status: TrayStatus, commands: CommandSink, logger: logging.Logger | None = None) -> None:
        import pystray

        self._pystray = pystray
        self._status = status
        self._commands = commands
        self.log = logger or logging.getLogger("switch2_presence.tray")
        self._kind = self._kind_for(status)
        self.icon = pystray.Icon(
            "switch2-presence",
            icon=load_icon(self._kind),
            title="Switch 2 Presence",
            menu=self._build_menu(),
        )

    # ---- lifecycle -------------------------------------------------------
    def run(self) -> None:
        self.icon.run()

    def start_detached(self) -> None:
        """Run the tray loop on its own thread so the main thread can host UI."""
        self.icon.run_detached()

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass

    def notify(self, message: str, title: str = "Switch 2 Presence") -> None:
        try:
            self.icon.notify(message, title)
        except Exception as exc:
            self.log.debug("tray notification failed: %s", exc)

    # ---- menu ------------------------------------------------------------
    def _kind_for(self, status: TrayStatus) -> str:
        disconnected = not status.obs_connected or (
            status.discord_enabled and not status.discord_connected
        )
        if status.error and disconnected and status.state != "OFF":
            return "error"
        if status.handheld:
            return "handheld"
        if status.state == "IN_GAME":
            return "game"
        if status.state in ("HOME", "UNKNOWN"):
            return "home"
        if status.state == "HANDHELD":
            return "handheld"
        return "off"

    def _send(self, name: str, *args: object) -> Callable[..., None]:
        def handler(icon: object, item: object) -> None:
            try:
                self._commands(name, *args)
            except Exception as exc:
                self.log.warning("tray command %s failed: %s", name, exc)

        return handler

    def _game_submenu(self) -> object:
        MenuItem = self._pystray.MenuItem
        Menu = self._pystray.Menu
        items = []
        for name in self._status.games[:20]:
            items.append(MenuItem(name, self._send("choose_game", name)))
        if not items:
            items.append(MenuItem("(no games detected yet)", None, enabled=False))
        return Menu(*items)

    def _build_menu(self) -> object:
        Menu = self._pystray.Menu
        MenuItem = self._pystray.MenuItem

        status_text = self._status.description
        if self._status.error:
            status_text = f"{status_text} — {self._status.error}"

        connections = []
        connections.append("OBS ✓" if self._status.obs_connected else "OBS ✗")
        if self._status.discord_enabled:
            connections.append("Discord ✓" if self._status.discord_connected else "Discord ✗")
        else:
            connections.append("Discord off")

        handheld_items = [
            MenuItem("Enable handheld", self._send("handheld_enable"), checked=lambda item: self._status.handheld),
            MenuItem("Choose game", self._game_submenu()),
            MenuItem("Resume last game", self._send("handheld_resume"), enabled=bool(self._status.last_game)),
            MenuItem('Generic "Playing Handheld"', self._send("handheld_generic")),
            MenuItem("Clear presence", self._send("handheld_clear")),
            Menu.SEPARATOR,
            MenuItem(
                "Automatic (dock)",
                self._send("handheld_disable"),
                checked=lambda item: not self._status.handheld,
            ),
        ]

        return Menu(
            MenuItem(status_text, None, enabled=False),
            MenuItem("  ".join(connections), None, enabled=False),
            Menu.SEPARATOR,
            MenuItem(
                "Paused" if self._status.paused else "Detection active",
                self._send("toggle_pause"),
                checked=lambda item: self._status.paused,
            ),
            MenuItem("Handheld mode", Menu(*handheld_items)),
            Menu.SEPARATOR,
            MenuItem("Run calibration…", self._send("calibrate")),
            MenuItem("Open logs", self._send("open_logs")),
            Menu.SEPARATOR,
            MenuItem("Quit", self._send("quit")),
        )

    # ---- updates ---------------------------------------------------------
    def update(self, status: TrayStatus) -> None:
        if status == self._status and self._kind == self._kind_for(status):
            return
        self._status = status
        kind = self._kind_for(status)
        if kind != self._kind:
            self._kind = kind
            self.icon.icon = load_icon(kind)
        self.icon.title = f"Switch 2 Presence — {status.description}"
        self.icon.menu = self._build_menu()
        try:
            self.icon.update_menu()
        except Exception as exc:
            self.log.debug("tray menu update failed: %s", exc)


def open_logs() -> None:
    try:
        from .winutil import open_in_explorer

        open_in_explorer(logs_dir())
    except Exception:
        pass
