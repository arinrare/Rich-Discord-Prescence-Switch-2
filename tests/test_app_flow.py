from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from switch2_presence.app import App
from switch2_presence.ocr import OcrReading
from switch2_presence.presence import PresencePayload


class FakeReader:
    available = True

    def __init__(self, reading: OcrReading) -> None:
        self.reading = reading
        self.calls = 0

    def read_title(self, frame: np.ndarray, region: list[int]) -> OcrReading:
        self.calls += 1
        return self.reading


class FakePresence:
    enabled = True
    connected = True
    error = None

    def __init__(self) -> None:
        self.calls: list[PresencePayload | None] = []

    def set(self, payload: PresencePayload | None, now: float | None = None, force: bool = False) -> bool:
        self.calls.append(payload)
        return True

    def clear(self, force: bool = False) -> bool:
        self.calls.append(None)
        return True

    def tick(self, now: float | None = None) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture()
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> App:
    monkeypatch.setenv("SWITCH2_PRESENCE_HOME", str(tmp_path))
    instance = App(tmp_path / "config.json", headless=True)
    instance.presence = FakePresence()  # type: ignore[assignment]
    return instance


def test_presence_follows_state(app: App) -> None:
    m = app.machine
    m.on_signal_on(0)
    m.on_home(1, "Mario Kart World")
    m.on_video(2)
    app._update_presence(3)
    payload = app.presence.calls[-1]  # type: ignore[attr-defined]
    assert payload is not None
    assert payload.details == "Playing Mario Kart World"
    assert payload.state == "Nintendo Switch 2"
    assert payload.start is not None

    m.on_home(20, "Mario Kart World")
    app._update_presence(21)
    payload = app.presence.calls[-1]  # type: ignore[attr-defined]
    assert payload is not None
    assert "HOME" in payload.state
    assert payload.start is None


def test_off_clears_presence_after_grace(app: App) -> None:
    m = app.machine
    m.on_signal_on(0)
    m.on_home(1, "Game")
    m.on_video(2)
    app._update_presence(3)
    app._on_off(10, "black hysteresis")
    sent = len(app.presence.calls)  # type: ignore[attr-defined]
    app._update_presence(30)
    assert len(app.presence.calls) == sent  # type: ignore[attr-defined]
    app._update_presence(10 + 61)
    assert app.presence.calls[-1] is None  # type: ignore[attr-defined]


def test_manual_handheld_flow(app: App) -> None:
    m = app.machine
    app.enqueue("choose_game", "Metroid Prime 4")
    app._drain_commands(100)
    assert m.state.value == "HANDHELD"
    payload = app.presence.calls[-1]  # type: ignore[attr-defined]
    assert payload is not None
    assert payload.details == "Playing Metroid Prime 4"
    assert "Handheld" in payload.state

    app.enqueue("handheld_generic")
    app._drain_commands(110)
    payload = app.presence.calls[-1]  # type: ignore[attr-defined]
    assert payload is not None
    assert payload.details == "Playing Handheld"

    app.enqueue("handheld_clear")
    app._drain_commands(120)
    assert app.presence.calls[-1] is None  # type: ignore[attr-defined]


def test_session_seconds_recorded(app: App) -> None:
    m = app.machine
    m.on_signal_on(0)
    m.on_home(1, "Game")
    m.on_video(2)
    app._update_presence(3)
    app._sync_session(3)
    app._sync_session(63)
    app._flush_session(63)
    entry = app.games.get("Game")
    assert entry is not None
    assert entry.total_seconds >= 60


def test_calibrate_routes_to_ui_queue(app: App) -> None:
    app.enqueue("calibrate")
    assert app.ui_commands.qsize() == 1
    assert app.commands.empty()


def test_quit_stops_immediately(app: App) -> None:
    app.enqueue("quit")
    assert app.stop_event.is_set()
    assert app.commands.empty()


def test_title_region_expands_to_full_width(app: App) -> None:
    assert app._title_region() == [0, 780, 1920, 60]


def test_provisional_title_needs_two_reads(app: App) -> None:
    reading = OcrReading("Provisional Game", "Provisional Game", None, False, 0.0)
    app.reader = FakeReader(reading)  # type: ignore[assignment]
    frame = np.zeros((80, 400, 3), dtype=np.uint8)

    assert app._read_title(10.0, frame) == "Provisional Game"
    assert app.games.get("Provisional Game") is None
    assert app._read_title(20.1, frame) == "Provisional Game"
    assert app.games.get("Provisional Game") is not None


def test_implausible_fragment_is_not_added(app: App) -> None:
    reading = OcrReading("Nintendo.", "Nintendo.", None, False, 0.0)
    app.reader = FakeReader(reading)  # type: ignore[assignment]
    frame = np.zeros((80, 400, 3), dtype=np.uint8)

    assert app._read_title(10.0, frame) is None
    assert app._read_title(20.1, frame) is None
    assert app.games.get("Nintendo.") is None


def test_unchanged_title_band_does_not_reocr_every_poll(app: App) -> None:
    reader = FakeReader(OcrReading("Game", "Game", None, False, 0.0))
    app.reader = reader  # type: ignore[assignment]
    frame = np.zeros((80, 400, 3), dtype=np.uint8)

    app._read_title(10.0, frame)
    app._read_title(12.0, frame)
    assert reader.calls == 1
    app._read_title(20.1, frame)
    assert reader.calls == 2


def test_home_browsing_presence_payload(app: App) -> None:
    m = app.machine
    m.on_signal_on(0)
    m.on_home(1, "Game A")
    m.on_video(2)
    app._update_presence(3)

    m.on_home(10, "Game B")
    app._update_presence(11)
    payload = app.presence.calls[-1]  # type: ignore[attr-defined]
    assert payload is not None
    assert payload.details == "On Switch 2 HOME menu"
    assert payload.state == "Browsing: Game B"
    assert "start" not in payload.to_kwargs()
    assert m.current_game == "Game A"
    assert m.active_seconds(20) == 8
