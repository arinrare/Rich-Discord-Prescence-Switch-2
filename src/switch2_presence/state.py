"""Pure state machine for console power / HOME / game / handheld transitions.

The machine has no Windows or I/O dependencies so it can be unit tested with
plain :class:`float` timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class GameState(StrEnum):
    OFF = "OFF"
    UNKNOWN = "UNKNOWN"
    HOME = "HOME"
    IN_GAME = "IN_GAME"
    HANDHELD = "HANDHELD"


class PresenceMode(StrEnum):
    OFF = "OFF"
    HOME_IDLE = "HOME_IDLE"
    HOME_MID_GAME = "HOME_MID_GAME"
    IN_GAME = "IN_GAME"
    HANDHELD_GAME = "HANDHELD_GAME"
    HANDHELD_GENERIC = "HANDHELD_GENERIC"


@dataclass
class StateSnapshot:
    last_game: str | None
    current_game: str | None
    selected_title: str | None
    active_seconds: float
    active_started_at: float | None
    handheld_game: str | None
    handheld_generic: bool

    def to_json(self, now: float) -> dict[str, Any]:
        return {
            "last_game": self.last_game,
            "current_game": self.current_game,
            "selected_title": self.selected_title,
            "active_seconds": round(self.active_seconds, 3),
            "active_started_at": self.active_started_at,
            "handheld_game": self.handheld_game,
            "handheld_generic": self.handheld_generic,
            "saved_at": now,
        }


class StateMachine:
    def __init__(self, freeze_on_home: bool = True, freeze_on_sleep: bool = True) -> None:
        self.freeze_on_home = freeze_on_home
        self.freeze_on_sleep = freeze_on_sleep
        self.state: GameState = GameState.OFF
        self.current_game: str | None = None
        self.last_game: str | None = None
        self.selected_title: str | None = None
        self.selected_is_system = False
        self.home_mid_game = False
        self.no_presence = False
        self.power_on_at: float | None = None
        self.handheld_game: str | None = None
        self.handheld_generic = False
        self._mode_before_handheld: GameState = GameState.OFF
        self._accumulated = 0.0
        self._started_at: float | None = None

    # ---- timer -----------------------------------------------------------
    @property
    def timer_running(self) -> bool:
        return self._started_at is not None

    def active_seconds(self, now: float) -> float:
        if self._started_at is None:
            return self._accumulated
        return self._accumulated + max(0.0, now - self._started_at)

    def start_timer(self, now: float) -> None:
        self._accumulated = 0.0
        self._started_at = now

    def reset_timer(self, now: float) -> None:
        self.start_timer(now)

    def freeze_timer(self, now: float, reason: str = "manual") -> None:
        if reason == "home" and not self.freeze_on_home:
            return
        if reason == "sleep" and not self.freeze_on_sleep:
            return
        if self._started_at is not None:
            self._accumulated = self.active_seconds(now)
            self._started_at = None

    def stop_timer(self, now: float) -> None:
        self.freeze_timer(now, "force")

    def resume_timer(self, now: float) -> None:
        if self._started_at is None:
            self._started_at = now

    # ---- signal ----------------------------------------------------------
    def on_signal_on(self, now: float) -> None:
        if self.state is GameState.HANDHELD:
            return
        if self.state is GameState.OFF or self.power_on_at is None:
            self.power_on_at = now
        self.state = GameState.UNKNOWN
        self.selected_title = None
        self.selected_is_system = False
        self.home_mid_game = False
        self.no_presence = False

    def on_signal_off(self, now: float) -> None:
        if self.state is GameState.HANDHELD:
            return
        self.state = GameState.OFF
        self.selected_title = None
        self.selected_is_system = False
        self.home_mid_game = False
        self.power_on_at = None
        self.freeze_timer(now, "sleep")

    # ---- console UI ------------------------------------------------------
    def on_home(
        self, now: float, selected_title: str | None = None, selected_is_system: bool = False
    ) -> None:
        if self.state is GameState.HANDHELD:
            return
        self.no_presence = False
        if selected_title:
            self.selected_title = selected_title
            self.selected_is_system = selected_is_system
        if self.state is GameState.IN_GAME and self.current_game:
            self.home_mid_game = True
            self.freeze_timer(now, "home")
        elif (self.current_game and self.home_mid_game) or not self.current_game:
            self.freeze_timer(now, "home")
        self.state = GameState.HOME

    def on_video(self, now: float) -> None:
        if self.state is GameState.HANDHELD:
            return
        self.no_presence = False
        if self.selected_is_system:
            self.selected_is_system = False
            self.current_game = None
            self.home_mid_game = False
            self.state = GameState.IN_GAME
            self.stop_timer(now)
            return
        target = self.selected_title

        if self.state is GameState.HOME and self.current_game and self.home_mid_game:
            if target is None or target == self.current_game:
                self.home_mid_game = False
                self.state = GameState.IN_GAME
                self.resume_timer(now)
                return

        if target:
            if target == self.current_game:
                self.last_game = target
                self.home_mid_game = False
                self.state = GameState.IN_GAME
                self.resume_timer(now)
                return
            self.current_game = target
            self.last_game = target
            self.home_mid_game = False
            self.state = GameState.IN_GAME
            self.start_timer(now)
            return

        if self.current_game:
            self.last_game = self.current_game
            self.home_mid_game = False
            self.state = GameState.IN_GAME
            self.resume_timer(now)
            return

        if self.last_game:
            self.current_game = self.last_game
            self.home_mid_game = False
            self.state = GameState.IN_GAME
            self.resume_timer(now)
            return

        self.home_mid_game = False
        self.state = GameState.IN_GAME
        self.stop_timer(now)

    # ---- manual ----------------------------------------------------------
    def manual_set_game(self, game: str, now: float) -> None:
        self.current_game = game
        self.last_game = game
        self.no_presence = False
        self.selected_is_system = False
        self.home_mid_game = False
        if self.state is GameState.HANDHELD:
            self.handheld_game = game
            self.handheld_generic = False
            self.start_timer(now)
        else:
            self.state = GameState.IN_GAME
            self.start_timer(now)

    def manual_clear(self, now: float) -> None:
        self.current_game = None
        self.no_presence = True
        self.selected_is_system = False
        self.home_mid_game = False
        if self.state is GameState.HANDHELD:
            self.handheld_game = None
            self.handheld_generic = True
        self.stop_timer(now)

    # ---- handheld --------------------------------------------------------
    def enter_handheld(self, now: float, game: str | None = None, generic: bool = False) -> None:
        if self.state is not GameState.HANDHELD:
            self._mode_before_handheld = self.state
        self.state = GameState.HANDHELD
        self.no_presence = False
        self.selected_is_system = False
        self.home_mid_game = False
        if game:
            self.current_game = game
            self.last_game = game
            self.handheld_game = game
            self.handheld_generic = False
        elif generic:
            self.current_game = None
            self.handheld_game = None
            self.handheld_generic = True
        else:
            self.handheld_game = self.current_game
            self.handheld_generic = self.current_game is None
        self.start_timer(now)

    def set_handheld_game(self, game: str, now: float, reset: bool = True) -> None:
        if self.state is not GameState.HANDHELD:
            self.enter_handheld(now, game=game)
            return
        self.current_game = game
        self.last_game = game
        self.handheld_game = game
        self.handheld_generic = False
        self.no_presence = False
        self.selected_is_system = False
        if reset:
            self.start_timer(now)
        else:
            self.resume_timer(now)

    def set_handheld_generic(self, now: float) -> None:
        if self.state is not GameState.HANDHELD:
            self.enter_handheld(now, generic=True)
            return
        self.current_game = None
        self.handheld_game = None
        self.handheld_generic = True
        self.no_presence = False
        self.selected_is_system = False
        self.start_timer(now)

    def resume_last_in_handheld(self, now: float) -> bool:
        if not self.last_game:
            self.set_handheld_generic(now)
            return False
        already = self.state is GameState.HANDHELD and self.current_game == self.last_game
        self.set_handheld_game(self.last_game, now, reset=not already)
        return True

    def leave_handheld(self, now: float, signal_on: bool) -> None:
        self.handheld_game = None
        self.handheld_generic = False
        self.selected_is_system = False
        self.home_mid_game = False
        self.no_presence = False
        self.freeze_timer(now, "force")
        self.state = GameState.UNKNOWN if signal_on else GameState.OFF
        if not signal_on:
            self.power_on_at = None

    # ---- derived ---------------------------------------------------------
    def _home_presence_game(self) -> str | None:
        """Game shown as mid-game on HOME, or None while browsing another title."""
        if not (self.home_mid_game and self.current_game):
            return None
        if self.selected_title and self.selected_title != self.current_game:
            return None
        return self.current_game

    def presence_mode(self) -> PresenceMode:
        if self.no_presence:
            return PresenceMode.OFF
        if self.state is GameState.HANDHELD:
            return PresenceMode.HANDHELD_GAME if self.current_game else PresenceMode.HANDHELD_GENERIC
        if self.state is GameState.HOME:
            return PresenceMode.HOME_MID_GAME if self._home_presence_game() else PresenceMode.HOME_IDLE
        if self.state is GameState.IN_GAME:
            return PresenceMode.IN_GAME
        return PresenceMode.OFF

    def presence_game(self) -> str | None:
        mode = self.presence_mode()
        if mode in (PresenceMode.HOME_MID_GAME, PresenceMode.IN_GAME, PresenceMode.HANDHELD_GAME):
            return self.current_game
        return None

    def describe(self) -> str:
        if self.state is GameState.OFF:
            return "Switch 2 off"
        if self.state is GameState.UNKNOWN:
            return "Waiting for signal…"
        if self.state is GameState.HANDHELD:
            if self.current_game:
                return f"Handheld — {self.current_game}"
            return "Handheld — Playing Handheld"
        if self.state is GameState.HOME:
            mid_game = self._home_presence_game()
            if mid_game:
                return f"{mid_game} (HOME menu)"
            if self.selected_title:
                return f"Browsing - {self.selected_title}"
            return "HOME menu"
        if self.current_game:
            return f"Playing {self.current_game}"
        return "On Switch 2"

    # ---- persistence -----------------------------------------------------
    def snapshot(self, now: float) -> StateSnapshot:
        return StateSnapshot(
            last_game=self.last_game,
            current_game=self.current_game,
            selected_title=self.selected_title,
            active_seconds=self.active_seconds(now),
            active_started_at=self._started_at,
            handheld_game=self.handheld_game,
            handheld_generic=self.handheld_generic,
        )

    def restore(self, data: dict[str, Any], now: float) -> None:
        if not isinstance(data, dict):
            return
        last = data.get("last_game")
        self.last_game = last if isinstance(last, str) and last else None
        current = data.get("current_game")
        self.current_game = current if isinstance(current, str) and current else None
        selected = data.get("selected_title")
        self.selected_title = selected if isinstance(selected, str) and selected else None
        self.selected_is_system = False
        try:
            accumulated = float(data.get("active_seconds") or 0.0)
        except (TypeError, ValueError):
            accumulated = 0.0
        self._accumulated = max(0.0, accumulated)
        self._started_at = None
        handheld_game = data.get("handheld_game")
        self.handheld_game = handheld_game if isinstance(handheld_game, str) and handheld_game else None
        self.handheld_generic = bool(data.get("handheld_generic", False))
        self.state = GameState.OFF
        self.home_mid_game = False
        self.power_on_at = None
