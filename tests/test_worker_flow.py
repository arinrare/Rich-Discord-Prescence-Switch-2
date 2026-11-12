from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from switch2_presence.app import App
from switch2_presence.presence import PresencePayload
from switch2_presence.vision import HomeDetector, crop_region


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


def bright(width: int, height: int) -> np.ndarray:
    rng = np.random.default_rng(width + height)
    return np.clip(110 + rng.normal(0, 30, (height, width, 3)), 0, 255).astype(np.uint8)


def black(width: int, height: int) -> np.ndarray:
    return np.full((height, width, 3), 2, dtype=np.uint8)


def home_screen(width: int, height: int, region: list[int]) -> np.ndarray:
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    scale = width / 1920.0
    x0, y0, w, h = (int(round(v * scale)) for v in region)
    rng = np.random.default_rng(3)
    frame[y0 : y0 + h, x0 : x0 + w] = np.clip(
        130 + rng.normal(0, 20, (h, w, 3)), 0, 255
    ).astype(np.uint8)
    return frame


@pytest.fixture()
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> App:
    monkeypatch.setenv("SWITCH2_PRESENCE_HOME", str(tmp_path))
    instance = App(tmp_path / "config.json", headless=True)
    instance.presence = FakePresence()  # type: ignore[assignment]
    instance.reader.disable("test")
    instance._home = HomeDetector(threshold=0.80, debounce_s=0.0, labels_required=3)
    region = instance.cfg["regions"]["dock"]
    instance._dock_template = crop_region(home_screen(1280, 720, region), region)
    instance.games.add("Mario Kart World", save=False)
    instance.machine.last_game = "Mario Kart World"
    return instance


def test_worker_power_home_game_off(app: App) -> None:
    mode = {"vision": "home"}

    def screenshot(width: int, height: int) -> np.ndarray:
        if width <= 320:
            return bright(width, height)
        if mode["vision"] == "home":
            return home_screen(width, height, app.cfg["regions"]["dock"])
        return bright(width, height)

    app.obs.screenshot = screenshot  # type: ignore[method-assign]

    app._poll_power(0.0)
    assert app.machine.state.value == "UNKNOWN"
    assert app._signal_on

    app._poll_vision(1.0)
    assert app.machine.state.value == "HOME"
    assert app.machine.presence_mode().value == "HOME_IDLE"

    mode["vision"] = "game"
    app._poll_vision(2.0)
    assert app.machine.state.value == "IN_GAME"
    assert app.machine.current_game == "Mario Kart World"
    payload = app.presence.calls[-1]  # type: ignore[attr-defined]
    assert payload is not None
    assert payload.details == "Playing Mario Kart World"

    def black_screenshot(width: int, height: int) -> np.ndarray:
        return black(width, height)

    app.obs.screenshot = black_screenshot  # type: ignore[method-assign]
    app._poll_power(20.0)
    app._poll_power(45.0)
    assert app.machine.state.value == "OFF"
    app._update_presence(106.0)
    assert app.presence.calls[-1] is None  # type: ignore[attr-defined]


def test_handheld_ignores_signal(app: App) -> None:
    app.enqueue("choose_game", "Mario Kart World")
    app._drain_commands(10.0)
    sent = len(app.presence.calls)  # type: ignore[attr-defined]

    app._on_off(11.0, "test")
    app._on_signal_on(12.0, "test")
    assert app.machine.state.value == "HANDHELD"
    assert len(app.presence.calls) == sent  # type: ignore[attr-defined]

    app.enqueue("handheld_disable")
    app._drain_commands(20.0)
    assert app.machine.state.value == "OFF"
