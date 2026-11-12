"""Interactive calibration wizard.

The wizard is designed to run on the application's main thread so OpenCV window
input (keyboard/mouse) is reliable. The tray runs detached meanwhile.
"""

from __future__ import annotations

import ctypes
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

import cv2
import numpy as np

from .config import dock_template_path
from .obs import OBSClient, OBSUnavailable
from .ocr import TitleReader
from .vision import crop_region, dock_score, expand_region_width, normalize_width, power_metrics

WINDOW = "Switch 2 Calibration"
MAX_WIDTH = 1920
MIN_REGION_PX = 8
_STOP = -1
_SPACE = 32
_ENTER = 13
_ESC = 27


def normalize_region(x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int]:
    left, right = sorted((int(x0), int(x1)))
    top, bottom = sorted((int(y0), int(y1)))
    return left, top, right - left, bottom - top


def clamp_region(region: Sequence[int], width: int, height: int) -> list[int]:
    x, y, w, h = (int(v) for v in region)
    x = max(0, min(x, max(0, width - 1)))
    y = max(0, min(y, max(0, height - 1)))
    w = max(0, min(w, width - x))
    h = max(0, min(h, height - y))
    return [x, y, w, h]


def region_is_valid(
    region: Sequence[int], width: int, height: int, min_size: int = MIN_REGION_PX
) -> bool:
    x, y, w, h = (int(v) for v in region)
    return w >= min_size and h >= min_size and x >= 0 and y >= 0 and x + w <= width and y + h <= height


def _activate_window(title: str = WINDOW) -> None:
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            user32.ShowWindow(hwnd, 5)  # SW_SHOW
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _window_visible(title: str = WINDOW) -> bool:
    try:
        return cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) >= 1
    except cv2.error:
        return False


def _show(canvas: np.ndarray, activate: bool = False) -> None:
    try:
        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
        cv2.imshow(WINDOW, canvas)
        cv2.setWindowProperty(WINDOW, cv2.WND_PROP_TOPMOST, 1)
        if activate:
            _activate_window()
    except cv2.error:
        pass


def _wait_key(wait_ms: int = 0, should_stop: Callable[[], bool] | None = None) -> int:
    """Wait for a key while staying responsive to quit and window close."""
    deadline = None if wait_ms <= 0 else time.monotonic() + wait_ms / 1000.0
    visible_once = False
    while True:
        if should_stop and should_stop():
            return _STOP
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return 0
            delay = max(1, min(50, int(remaining * 1000)))
        else:
            delay = 50
        try:
            key = cv2.waitKey(delay) & 0xFF
        except cv2.error:
            return _STOP
        if key != 255:
            return key
        if _window_visible():
            visible_once = True
        elif visible_once:
            return _STOP


def _draw_message(frame: np.ndarray, lines: Sequence[str]) -> np.ndarray:
    canvas = frame.copy()
    height = 30 * len(lines) + 14
    overlay = canvas.copy()
    cv2.rectangle(overlay, (0, 0), (canvas.shape[1], height), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.88, canvas, 0.12, 0, canvas)
    cv2.line(canvas, (0, height), (canvas.shape[1], height), (120, 200, 255), 2)
    for index, line in enumerate(lines):
        cv2.putText(
            canvas,
            line,
            (16, 26 + index * 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.68,
            (245, 245, 245),
            2,
            cv2.LINE_AA,
        )
    return canvas


class _RegionSelector:
    """Mouse ROI selector with a high-contrast double outline."""

    def __init__(
        self,
        frame: np.ndarray,
        lines: Sequence[str],
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        self.frame = frame
        self.height, self.width = frame.shape[:2]
        self.lines = list(lines)
        self.should_stop = should_stop
        self.start: tuple[int, int] | None = None
        self.end: tuple[int, int] | None = None
        self.dragging = False
        self.hint: str | None = None

    def _clamp_point(self, x: int, y: int) -> tuple[int, int]:
        return max(0, min(int(x), self.width - 1)), max(0, min(int(y), self.height - 1))

    def _region(self) -> list[int] | None:
        if self.start is None or self.end is None:
            return None
        x, y, w, h = normalize_region(*self.start, *self.end)
        return clamp_region([x, y, w, h], self.width, self.height)

    def _on_mouse(self, event: int, x: int, y: int, flags: int, param: object) -> None:
        x, y = self._clamp_point(x, y)
        if event == cv2.EVENT_LBUTTONDOWN:
            self.start = (x, y)
            self.end = (x, y)
            self.dragging = True
            self.hint = None
        elif event == cv2.EVENT_MOUSEMOVE and self.dragging:
            self.end = (x, y)
        elif event == cv2.EVENT_LBUTTONUP and self.dragging:
            self.end = (x, y)
            self.dragging = False

    def _draw_selection(self, canvas: np.ndarray, header_height: int = 0) -> np.ndarray:
        if self.start is None or self.end is None:
            return canvas

        if self.start == self.end:
            x, y = self.start
            cv2.line(canvas, (x, 0), (x, self.height - 1), (0, 0, 0), 3, cv2.LINE_AA)
            cv2.line(canvas, (x, 0), (x, self.height - 1), (255, 255, 255), 1, cv2.LINE_AA)
            cv2.line(canvas, (0, y), (self.width - 1, y), (0, 0, 0), 3, cv2.LINE_AA)
            cv2.line(canvas, (0, y), (self.width - 1, y), (255, 255, 255), 1, cv2.LINE_AA)
            return canvas

        region = self._region()
        if region is None:
            return canvas
        x, y, w, h = region
        x1, y1 = min(self.width, x + w), min(self.height, y + h)

        dimmed = cv2.convertScaleAbs(canvas, alpha=0.55, beta=0)
        dimmed[y:y1, x:x1] = canvas[y:y1, x:x1]
        if header_height:
            dimmed[:header_height, :] = canvas[:header_height, :]
        canvas = dimmed

        # Requirement: 2 px dark + 2 px light outline, visible on any content.
        cv2.rectangle(canvas, (x - 2, y - 2), (x1 + 2, y1 + 2), (0, 0, 0), 2)
        cv2.rectangle(canvas, (x, y), (x1, y1), (255, 255, 255), 2)

        label = f"{w} x {h} px"
        (text_w, text_h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        label_y = y + h + text_h + 10 if y + h + text_h + 10 < self.height else max(20, y - 8)
        label_x = max(0, min(x, self.width - text_w - 12))
        cv2.rectangle(
            canvas,
            (label_x, label_y - text_h - 6),
            (label_x + text_w + 10, label_y + 4),
            (0, 0, 0),
            -1,
        )
        cv2.putText(
            canvas,
            label,
            (label_x + 5, label_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        return canvas

    def _render(self) -> None:
        lines = [
            *self.lines,
            "Drag a box.  SPACE/ENTER = accept   R = reset   ESC = cancel",
        ]
        if self.hint:
            lines.append(self.hint)
        header_height = 30 * len(lines) + 14
        canvas = _draw_message(self.frame, lines)
        canvas = self._draw_selection(canvas, header_height)
        _show(canvas)

    def run(self) -> list[int] | None:
        try:
            cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
            cv2.setMouseCallback(WINDOW, self._on_mouse)
            self._render()
            _activate_window()
            while True:
                self._render()
                key = _wait_key(30, self.should_stop)
                if key in (_STOP, _ESC):
                    return None
                if key in (_ENTER, _SPACE):
                    region = self._region()
                    if region is not None and region_is_valid(region, self.width, self.height):
                        return region
                    self.hint = (
                        f"Selection too small or outside the frame (min {MIN_REGION_PX} px). "
                        "Drag a larger box."
                    )
                elif key in (ord("r"), ord("R")):
                    self.start = None
                    self.end = None
                    self.dragging = False
                    self.hint = None
        except cv2.error:
            return None
        finally:
            self.dragging = False


def _select_region(
    frame: np.ndarray,
    lines: Sequence[str],
    should_stop: Callable[[], bool] | None = None,
) -> list[int] | None:
    return _RegionSelector(frame, lines, should_stop).run()


def _message(
    frame: np.ndarray,
    lines: Sequence[str],
    wait_ms: int = 0,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    _show(_draw_message(frame, lines), activate=True)
    return _wait_key(wait_ms, should_stop)


def _capture(obs: OBSClient, width: int = 320, height: int = 180) -> np.ndarray:
    return obs.screenshot(width, height)


def _sample_power(
    obs: OBSClient,
    frame: np.ndarray,
    lines: Sequence[str],
    count: int = 30,
    should_stop: Callable[[], bool] | None = None,
) -> list[tuple[float, float, float]] | None:
    key = _message(
        frame,
        [*lines, "SPACE/ENTER = start sampling   ESC = skip"],
        should_stop=should_stop,
    )
    if key in (_ESC, _STOP):
        return None
    samples: list[tuple[float, float, float]] = []
    for index in range(count):
        if should_stop and should_stop():
            return None
        try:
            image = _capture(obs)
        except OBSUnavailable:
            time.sleep(0.2)
            continue
        metrics = power_metrics(image)
        samples.append((metrics.mean, metrics.std, metrics.edges))
        status = _draw_message(
            frame,
            [
                f"Sampling {index + 1}/{count} - mean {metrics.mean:.1f} std {metrics.std:.1f}",
                "Keep the console in this state. ESC = stop.",
            ],
        )
        _show(status)
        key = _wait_key(200, should_stop)
        if key == _STOP:
            return None
        if key == _ESC:
            break
    return samples or None


def _derive_black_thresholds(samples: Sequence[tuple[float, float, float]]) -> tuple[float, float]:
    means = [s[0] for s in samples]
    stds = [s[1] for s in samples]
    mean_max = max(12.0, max(means) + 3.0)
    std_max = max(4.0, max(stds) + 2.0)
    return round(mean_max, 1), round(std_max, 1)


def _derive_on_thresholds(
    samples: Sequence[tuple[float, float, float]], black_mean: float, black_std: float
) -> tuple[float, float]:
    means = sorted(s[0] for s in samples)
    stds = sorted(s[1] for s in samples)
    low_mean = means[max(0, len(means) // 10)] if means else 40.0
    low_std = stds[max(0, len(stds) // 10)] if stds else 15.0
    on_mean = max(25.0, black_mean + 5.0, low_mean - 8.0)
    on_std = max(10.0, black_std + 2.0, low_std - 4.0)
    return round(on_mean, 1), round(on_std, 1)


def _run_wizard_inner(
    obs: OBSClient,
    config: dict[str, Any],
    reader: TitleReader,
    logger: logging.Logger,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any] | None:
    """Run the calibration flow. Returns the updated config, or None if cancelled."""

    def announce(text: str) -> None:
        logger.info("Calibration: %s", text)

    stop = should_stop or (lambda: False)

    announce("Capturing a frame from OBS")
    try:
        frame = normalize_width(obs.screenshot(MAX_WIDTH, 1080), MAX_WIDTH)
    except OBSUnavailable as exc:
        announce(f"OBS not available: {exc}")
        return None
    if stop():
        return None

    base = frame.copy()
    regions: dict[str, list[int]] = {}

    announce("Select the title text region")
    title = _select_region(
        frame,
        [
            "Step 1/3 - Title text",
            "Put the console on the HOME menu with a game selected.",
            "Drag a box around the game title text (just its height).",
            "The search band will span the full screen width.",
        ],
        stop,
    )
    if title is None:
        cv2.destroyAllWindows()
        return None
    regions["title"] = expand_region_width(title, MAX_WIDTH)

    announce("Select the selected-game icon region")
    icon = _select_region(
        frame,
        [
            "Step 2/3 - Selected game icon (optional)",
            "Drag a box around the large icon of the selected game.",
            "Press ESC to skip this step.",
        ],
        stop,
    )
    if icon is not None:
        regions["icon"] = icon

    announce("Select the bottom dock region")
    dock = _select_region(
        frame,
        [
            "Step 3/3 - Bottom dock",
            "Drag a box around the icon dock (eShop / Album / Settings / ...).",
        ],
        stop,
    )
    if dock is None:
        cv2.destroyAllWindows()
        return None
    regions["dock"] = dock

    try:
        template = crop_region(base, dock) if dock else None
    except Exception as exc:
        logger.warning("Could not crop dock template: %s", exc)
        template = None

    announce("Power calibration: turn the console OFF")
    black = _sample_power(
        obs,
        base,
        [
            "Power calibration 1/2 - OFF",
            "Turn the console fully off or put it to sleep.",
            "Black frames may take up to 20 s to settle.",
        ],
        should_stop=stop,
    )
    if stop():
        cv2.destroyAllWindows()
        return None

    power_done = False
    too_bright = bool(black) and max(sample[0] for sample in black) > 40
    if black is not None:
        config.setdefault("analysis", {})["freeze_detection_enabled"] = too_bright
    if too_bright:
        announce(
            "Black frames look bright; the capture card may hold the last frame. "
            "Enabling frozen-frame detection as a fallback."
        )
    elif black:
        black_mean, black_std = _derive_black_thresholds(black)
        announce("Power calibration: turn the console ON to HOME")
        on = _sample_power(
            obs,
            base,
            [
                "Power calibration 2/2 - ON at HOME",
                "Wake the console and leave it on the HOME menu.",
            ],
            should_stop=stop,
        )
        if stop():
            cv2.destroyAllWindows()
            return None
        if on:
            on_mean, on_std = _derive_on_thresholds(on, black_mean, black_std)
            analysis = config.setdefault("analysis", {})
            analysis["black_mean_max"] = black_mean
            analysis["black_std_max"] = black_std
            analysis["on_mean_min"] = on_mean
            analysis["on_std_min"] = on_std
            power_done = True
            logger.info(
                "Power thresholds: black mean<=%.1f std<=%.1f, on mean>=%.1f std>=%.1f",
                black_mean,
                black_std,
                on_mean,
                on_std,
            )

    announce("Testing HOME detection and OCR")
    try:
        test_frame = normalize_width(obs.screenshot(MAX_WIDTH, 1080), MAX_WIDTH)
        score = 0.0
        if template is not None:
            score = dock_score(test_frame, template, regions["dock"])
        reading = reader.read_title(test_frame, regions["title"])
        lines = [
            f"dock match: {score:.2f}  (threshold {config['analysis'].get('home_match_threshold', 0.8):.2f})",
            f"OCR: {reading.clean or '(nothing)'}",
            f"match: {reading.match.canonical if reading.match else '(none)'} "
            f"score {reading.score:.0f}",
            f"power thresholds {'updated' if power_done else 'unchanged'}",
            "Press any key to close.",
        ]
        _message(test_frame, lines, should_stop=stop)
    except OBSUnavailable as exc:
        announce(f"Test skipped: {exc}")
    finally:
        cv2.destroyAllWindows()

    if stop():
        return None

    if template is not None and template.size:
        try:
            path = dock_template_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            if cv2.imwrite(str(path), template):
                logger.info("Saved dock template to %s", path)
            else:
                logger.warning("Could not write dock template to %s", path)
        except Exception as exc:
            logger.warning("Could not save dock template: %s", exc)

    config.setdefault("regions", {}).update(regions)
    announce("Calibration complete")
    return config


def run_wizard(
    obs: OBSClient,
    config: dict[str, Any],
    reader: TitleReader,
    logger: logging.Logger,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any] | None:
    """Run calibration with guaranteed window cleanup."""
    try:
        return _run_wizard_inner(obs, config, reader, logger, should_stop)
    finally:
        try:
            cv2.destroyAllWindows()
        except cv2.error:
            pass
