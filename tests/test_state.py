from __future__ import annotations

from switch2_presence.state import GameState, PresenceMode, StateMachine


def test_cold_boot_home_launch_home_resume() -> None:
    m = StateMachine()
    assert m.state is GameState.OFF
    assert m.presence_mode() is PresenceMode.OFF

    m.on_signal_on(0)
    assert m.state is GameState.UNKNOWN
    assert m.power_on_at == 0

    m.on_home(1, "Mario Kart World")
    assert m.state is GameState.HOME
    assert m.selected_title == "Mario Kart World"
    assert m.presence_mode() is PresenceMode.HOME_IDLE

    m.on_video(2)
    assert m.state is GameState.IN_GAME
    assert m.current_game == "Mario Kart World"
    assert m.last_game == "Mario Kart World"
    assert m.presence_mode() is PresenceMode.IN_GAME
    assert m.active_seconds(10) == 8

    m.on_home(20, "Mario Kart World")
    assert m.state is GameState.HOME
    assert m.home_mid_game is True
    assert m.presence_mode() is PresenceMode.HOME_MID_GAME
    assert m.active_seconds(30) == 18  # frozen

    m.on_video(40)
    assert m.state is GameState.IN_GAME
    assert m.presence_mode() is PresenceMode.IN_GAME
    assert m.active_seconds(50) == 28  # resumed


def test_home_to_different_game_resets_timer() -> None:
    m = StateMachine()
    m.on_signal_on(0)
    m.on_home(1, "Mario Kart World")
    m.on_video(2)
    assert m.active_seconds(60) == 58

    m.on_home(60, "The Legend of Zelda")
    assert m.presence_mode() is PresenceMode.HOME_IDLE
    assert m.selected_title == "The Legend of Zelda"
    m.on_video(61)
    assert m.current_game == "The Legend of Zelda"
    assert m.last_game == "The Legend of Zelda"
    assert m.active_seconds(61) == 0
    assert m.active_seconds(71) == 10


def test_sleep_freezes_and_wake_resumes_last_game() -> None:
    m = StateMachine()
    m.on_signal_on(0)
    m.on_home(1, "Mario Kart World")
    m.on_video(2)
    assert m.active_seconds(40) == 38

    m.on_signal_off(40)
    assert m.state is GameState.OFF
    assert m.presence_mode() is PresenceMode.OFF
    assert m.active_seconds(100) == 38
    assert m.last_game == "Mario Kart World"

    m.on_signal_on(100)
    assert m.state is GameState.UNKNOWN
    m.on_video(101)
    assert m.state is GameState.IN_GAME
    assert m.current_game == "Mario Kart World"
    assert m.active_seconds(111) == 48


def test_home_idle_does_not_run_timer() -> None:
    m = StateMachine()
    m.on_signal_on(0)
    m.on_home(1, "Splatoon 4")
    assert not m.timer_running
    assert m.active_seconds(500) == 0


def test_manual_set_and_clear() -> None:
    m = StateMachine()
    m.manual_set_game("Hades II", 100)
    assert m.state is GameState.IN_GAME
    assert m.current_game == "Hades II"
    assert m.active_seconds(110) == 10

    m.manual_clear(120)
    assert m.current_game is None
    assert m.presence_mode() is PresenceMode.OFF
    assert m.no_presence is True
    assert m.active_seconds(200) == 20  # frozen


def test_handheld_actions_and_return_to_auto() -> None:
    m = StateMachine()
    m.enter_handheld(0)
    assert m.state is GameState.HANDHELD
    assert m.presence_mode() is PresenceMode.HANDHELD_GENERIC

    m.set_handheld_game("Metroid Prime 4", 10)
    assert m.presence_mode() is PresenceMode.HANDHELD_GAME
    assert m.active_seconds(20) == 10

    m.on_signal_off(30)  # ignored while handheld
    assert m.state is GameState.HANDHELD

    m.set_handheld_generic(30)
    assert m.presence_mode() is PresenceMode.HANDHELD_GENERIC
    assert m.active_seconds(40) == 10

    m.leave_handheld(40, signal_on=False)
    assert m.state is GameState.OFF
    assert m.presence_mode() is PresenceMode.OFF

    m.on_signal_on(50)
    assert m.state is GameState.UNKNOWN
    m.leave_handheld(50, signal_on=True)
    assert m.state is GameState.UNKNOWN


def test_handheld_resume_last() -> None:
    m = StateMachine()
    m.last_game = "Donkey Kong Bananza"
    assert m.resume_last_in_handheld(5)
    assert m.state is GameState.HANDHELD
    assert m.current_game == "Donkey Kong Bananza"
    assert m.presence_mode() is PresenceMode.HANDHELD_GAME


def test_freeze_on_home_disabled_keeps_timer() -> None:
    m = StateMachine(freeze_on_home=False)
    m.on_signal_on(0)
    m.on_home(1, "Game")
    m.on_video(2)
    m.on_home(12, "Game")
    assert m.presence_mode() is PresenceMode.HOME_MID_GAME
    assert m.active_seconds(20) == 18


def test_wake_without_home_uses_last_game() -> None:
    m = StateMachine()
    m.last_game = "Mario Kart World"
    m.on_signal_on(0)
    m.on_video(3)
    assert m.state is GameState.IN_GAME
    assert m.current_game == "Mario Kart World"


def test_snapshot_restore() -> None:
    m = StateMachine()
    m.on_signal_on(0)
    m.on_home(1, "Game")
    m.on_video(2)
    snapshot = m.snapshot(10)
    assert snapshot.active_seconds == 8
    assert snapshot.last_game == "Game"

    restored = StateMachine()
    restored.restore(snapshot.to_json(10), 50)
    assert restored.state is GameState.OFF
    assert restored.last_game == "Game"
    assert restored.current_game == "Game"
    assert restored.selected_title == "Game"
    assert restored.active_seconds(100) == 8


def test_presence_game_only_for_active_modes() -> None:
    m = StateMachine()
    assert m.presence_game() is None
    m.enter_handheld(0, game="Game")
    assert m.presence_game() == "Game"
    m.set_handheld_generic(1)
    assert m.presence_game() is None


def test_system_app_launch_drops_game_claim() -> None:
    m = StateMachine()
    m.on_signal_on(0)
    m.on_home(1, "Mario Kart World")
    m.on_video(2)
    assert m.active_seconds(20) == 18

    m.on_home(20, "eShop", selected_is_system=True)
    assert m.state is GameState.HOME
    assert m.presence_mode() is PresenceMode.HOME_IDLE
    assert m.presence_game() is None

    m.on_video(30)
    assert m.state is GameState.IN_GAME
    assert m.current_game is None
    assert m.last_game == "Mario Kart World"
    assert m.presence_game() is None
    assert m.active_seconds(50) == 18  # frozen, not claimed


def test_home_browsing_after_game_switches_presence() -> None:
    m = StateMachine()
    m.on_signal_on(0)
    m.on_home(1, "Game A")
    m.on_video(2)
    assert m.active_seconds(20) == 18

    m.on_home(20, "Game A")
    assert m.presence_mode() is PresenceMode.HOME_MID_GAME
    assert "HOME menu" in m.describe()

    m.on_home(22, "Game B")
    assert m.presence_mode() is PresenceMode.HOME_IDLE
    assert m.presence_game() is None
    assert m.current_game == "Game A"
    assert m.active_seconds(40) == 18  # still frozen while browsing
    assert m.describe() == "Browsing - Game B"

    m.on_home(42, "Game A")
    assert m.presence_mode() is PresenceMode.HOME_MID_GAME
    assert m.describe() == "Game A (HOME menu)"

    m.selected_title = None
    assert m.presence_mode() is PresenceMode.HOME_MID_GAME
