from __future__ import annotations

from switch2_presence.presence import PresenceManager, build_payload
from switch2_presence.state import PresenceMode


def test_in_game_payload() -> None:
    payload = build_payload(PresenceMode.IN_GAME, "Mario Kart World", 1234)
    assert payload is not None
    kwargs = payload.to_kwargs()
    assert kwargs["details"] == "Playing Mario Kart World"
    assert kwargs["state"] == "Nintendo Switch 2"
    assert kwargs["large_image"] == "switch2"
    assert kwargs["start"] == 1234


def test_home_idle_payload() -> None:
    payload = build_payload(PresenceMode.HOME_IDLE, None, None, home_selected="Zelda")
    assert payload is not None
    assert payload.details == "On Switch 2 HOME menu"
    assert payload.state == "Browsing: Zelda"
    assert "start" not in payload.to_kwargs()


def test_handheld_payloads() -> None:
    game = build_payload(PresenceMode.HANDHELD_GAME, "Game", 50)
    assert game is not None
    assert game.state == "Nintendo Switch 2 • Handheld"
    generic = build_payload(PresenceMode.HANDHELD_GENERIC, None, 50)
    assert generic is not None
    assert generic.details == "Playing Handheld"


def test_off_payload_clears() -> None:
    assert build_payload(PresenceMode.OFF, "Game", 123) is None


def test_disabled_manager_is_noop() -> None:
    manager = PresenceManager("", min_interval=15)
    assert not manager.enabled
    assert manager.set(build_payload(PresenceMode.IN_GAME, "Game", 1)) is False
    manager.tick(0)
    manager.close()


def test_manager_throttles_updates() -> None:
    class FakeRpc:
        def __init__(self) -> None:
            self.calls: list[object] = []

        def update(self, **kwargs: object) -> None:
            self.calls.append(kwargs)

        def clear(self) -> None:
            self.calls.append(None)

        def close(self) -> None:
            pass

    manager = PresenceManager("12345", min_interval=15)
    rpc = FakeRpc()
    manager._rpc = rpc
    manager._connected = True

    p1 = build_payload(PresenceMode.IN_GAME, "A", 100)
    p2 = build_payload(PresenceMode.IN_GAME, "B", 200)
    assert manager.set(p1, now=0)
    assert manager.set(p1, now=1)
    assert not manager.set(p2, now=5)
    assert len(rpc.calls) == 1
    manager.tick(now=16)
    assert len(rpc.calls) == 2
    assert rpc.calls[0]["details"] == "Playing A"
    assert rpc.calls[1]["details"] == "Playing B"


def test_payload_fields_are_clipped() -> None:
    long_title = "A" * 300
    payload = build_payload(PresenceMode.IN_GAME, long_title, 1)
    assert payload is not None
    kwargs = payload.to_kwargs()
    assert len(kwargs["details"]) <= 128
    assert len(kwargs["large_text"]) <= 128
