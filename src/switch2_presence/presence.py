"""Discord Rich Presence payloads and a throttled pypresence manager."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from .state import PresenceMode

_TEXT_LIMIT = 128


def _clip(text: str) -> str:
    if len(text) <= _TEXT_LIMIT:
        return text
    return text[: _TEXT_LIMIT - 1] + "…"


@dataclass(frozen=True)
class PresencePayload:
    details: str
    state: str
    large_image: str = "switch2"
    large_text: str = ""
    small_image: str = ""
    small_text: str = ""
    start: int | None = None

    def to_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "details": _clip(self.details),
            "state": _clip(self.state),
            "large_image": self.large_image,
            "large_text": _clip(self.large_text or self.details),
        }
        if self.small_image:
            kwargs["small_image"] = self.small_image
        if self.small_text:
            kwargs["small_text"] = _clip(self.small_text)
        if self.start is not None:
            kwargs["start"] = int(self.start)
        return kwargs


def build_payload(
    mode: PresenceMode,
    game: str | None,
    start_ts: int | None,
    *,
    large_image: str = "switch2",
    small_image: str = "",
    home_selected: str | None = None,
    power_on_ts: int | None = None,
    freeze_on_home: bool = True,
) -> PresencePayload | None:
    """Map a presence mode to a Discord payload, or None to clear."""
    if mode is PresenceMode.OFF:
        return None
    if mode is PresenceMode.IN_GAME:
        if game:
            return PresencePayload(
                details=f"Playing {game}",
                state="Nintendo Switch 2",
                large_image=large_image,
                large_text=game,
                small_image=small_image,
                start=start_ts,
            )
        return PresencePayload(
            details="On Nintendo Switch 2",
            state="Nintendo Switch 2",
            large_image=large_image,
            large_text="Nintendo Switch 2",
            small_image=small_image,
        )
    if mode is PresenceMode.HOME_MID_GAME:
        return PresencePayload(
            details=f"Playing {game}",
            state="Nintendo Switch 2 • HOME menu",
            large_image=large_image,
            large_text=game or "Nintendo Switch 2",
            small_image=small_image,
            start=None if freeze_on_home else start_ts,
        )
    if mode is PresenceMode.HOME_IDLE:
        state = f"Browsing: {home_selected}" if home_selected else "Nintendo Switch 2"
        return PresencePayload(
            details="On Switch 2 HOME menu",
            state=state,
            large_image=large_image,
            large_text="Nintendo Switch 2",
            small_image=small_image,
            start=power_on_ts,
        )
    if mode is PresenceMode.HANDHELD_GAME:
        return PresencePayload(
            details=f"Playing {game}" if game else "Playing Handheld",
            state="Nintendo Switch 2 • Handheld",
            large_image=large_image,
            large_text=game or "Nintendo Switch 2",
            small_image=small_image,
            start=start_ts,
        )
    if mode is PresenceMode.HANDHELD_GENERIC:
        return PresencePayload(
            details="Playing Handheld",
            state="Nintendo Switch 2",
            large_image=large_image,
            large_text="Nintendo Switch 2",
            small_image=small_image,
            start=start_ts,
        )
    return None


class PresenceManager:
    def __init__(
        self,
        client_id: str,
        large_image: str = "switch2",
        small_image: str = "",
        min_interval: float = 15.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self.client_id = client_id.strip()
        self.large_image = large_image
        self.small_image = small_image
        self.min_interval = max(1.0, float(min_interval))
        self.log = logger or logging.getLogger("switch2_presence.presence")
        self._rpc: Any = None
        self._connected = False
        self._last_sent = 0.0
        self._last_payload: PresencePayload | None = None
        self._has_sent = False
        self._pending_set = False
        self._pending: PresencePayload | None = None
        self._next_reconnect = 0.0
        self.error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.client_id)

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        if not self.enabled:
            self.error = "Discord client ID not configured"
            return False
        try:
            from pypresence import Presence

            rpc = Presence(self.client_id)
            rpc.connect()
            self._rpc = rpc
            self._connected = True
            self.error = None
            self.log.info("Discord connected")
            return True
        except Exception as exc:
            self._connected = False
            self.error = str(exc) or exc.__class__.__name__
            self._next_reconnect = time.monotonic() + 10.0
            self.log.warning("Discord connect failed: %s", self.error)
            return False

    def _disconnect(self) -> None:
        if self._rpc is not None:
            try:
                self._rpc.close()
            except Exception:
                pass
        self._rpc = None
        self._connected = False

    def _send(self, payload: PresencePayload | None, now: float) -> bool:
        if not self._connected:
            if now < self._next_reconnect:
                return False
            if not self.connect():
                return False
        try:
            if payload is None:
                self._rpc.clear()
            else:
                self._rpc.update(**payload.to_kwargs())
            self._last_payload = payload
            self._has_sent = True
            self._last_sent = now
            self._pending_set = False
            self._pending = None
            self.error = None
            return True
        except Exception as exc:
            self.error = str(exc) or exc.__class__.__name__
            self.log.warning("Discord update failed: %s", self.error)
            self._disconnect()
            self._next_reconnect = now + 10.0
            return False

    def set(self, payload: PresencePayload | None, now: float | None = None, force: bool = False) -> bool:
        """Request a presence state. Returns True if sent immediately."""
        now = time.monotonic() if now is None else now
        if not self.enabled:
            return False
        if self._has_sent and payload == self._last_payload and not self._pending_set:
            return True
        if force or not self._has_sent or now - self._last_sent >= self.min_interval:
            return self._send(payload, now)
        self._pending_set = True
        self._pending = payload
        return False

    def tick(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if not self.enabled:
            return
        if not self._connected:
            if now < self._next_reconnect:
                return
            if not self.connect():
                return
            if self._pending_set:
                self._send(self._pending, now)
            elif self._has_sent:
                self._send(self._last_payload, now)
            return
        if self._pending_set and now - self._last_sent >= self.min_interval:
            self._send(self._pending, now)

    def clear(self, force: bool = False) -> bool:
        return self.set(None, time.monotonic()) if not force else self._send(None, time.monotonic())

    def close(self) -> None:
        if self._connected:
            try:
                self._rpc.clear()
            except Exception:
                pass
        self._disconnect()
