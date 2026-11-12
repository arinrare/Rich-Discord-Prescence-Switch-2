"""Handheld-mode actions shared by the tray and the worker."""

from __future__ import annotations

from enum import StrEnum

from .games import GameDB
from .state import StateMachine


class HandheldAction(StrEnum):
    ENABLE = "enable"
    DISABLE = "disable"
    CHOOSE_GAME = "choose_game"
    RESUME_LAST = "resume_last"
    GENERIC = "generic"
    CLEAR = "clear"


def apply(
    machine: StateMachine,
    games: GameDB | None,
    action: HandheldAction,
    now: float,
    game: str | None = None,
    signal_on: bool = False,
) -> str:
    """Apply a handheld action and return a human-readable result."""
    if action is HandheldAction.ENABLE:
        machine.enter_handheld(now, game=game)
        return "Handheld mode enabled"
    if action is HandheldAction.DISABLE:
        machine.leave_handheld(now, signal_on=signal_on)
        return "Automatic mode"
    if action is HandheldAction.CHOOSE_GAME:
        if not game:
            return "No game selected"
        machine.set_handheld_game(game, now)
        if games:
            games.touch(game)
        return f"Handheld — {game}"
    if action is HandheldAction.RESUME_LAST:
        if machine.resume_last_in_handheld(now):
            if games and machine.current_game:
                games.touch(machine.current_game)
            return f"Handheld — {machine.current_game}"
        return "Handheld — Playing Handheld"
    if action is HandheldAction.GENERIC:
        machine.set_handheld_generic(now)
        return "Handheld — Playing Handheld"
    if action is HandheldAction.CLEAR:
        machine.manual_clear(now)
        return "Presence cleared"
    return "No change"


def persisted(machine: StateMachine) -> dict[str, object]:
    return {
        "enabled": machine.state.value == "HANDHELD",
        "game": machine.handheld_game,
        "generic": machine.handheld_generic,
    }
