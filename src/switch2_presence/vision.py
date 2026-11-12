"""Frame decoding, power metrics, HOME detection, and OCR preprocessing."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np

DEFAULT_BASE_WIDTH = 1920


def decode_data_uri(data_uri: str) -> np.ndarray:
    """Decode an OBS ``data:image/png;base64,...`` screenshot into BGR."""
    payload = data_uri.split(",", 1)[1] if "," in data_uri else data_uri
    raw = base64.b64decode(payload)
    return decode_bytes(raw)


def decode_bytes(raw: bytes) -> np.ndarray:
    buffer = np.frombuffer(raw, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("could not decode image")
    return image


def to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


@dataclass(frozen=True)
class PowerMetrics:
    mean: float
    std: float
    edges: float


def power_metrics(image: np.ndarray) -> PowerMetrics:
    gray = to_gray(image)
    mean, std = cv2.meanStdDev(gray)
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    return PowerMetrics(
        mean=float(mean[0][0]),
        std=float(std[0][0]),
        edges=float(laplacian.var()),
    )


def diff_score(a: np.ndarray, b: np.ndarray, size: tuple[int, int] = (160, 90)) -> float:
    """Mean absolute grayscale difference of two frames, normalized to 160x90."""
    ga = cv2.resize(to_gray(a), size, interpolation=cv2.INTER_AREA)
    gb = cv2.resize(to_gray(b), size, interpolation=cv2.INTER_AREA)
    return float(np.mean(cv2.absdiff(ga, gb)))


@dataclass(frozen=True)
class PowerReading:
    state: str | None  # "on", "off", or None when not yet confirmed
    reason: str
    frozen: bool


class PowerDetector:
    """Black-frame / on-frame hysteresis plus frozen-frame fallback."""

    def __init__(
        self,
        black_mean_max: float = 12.0,
        black_std_max: float = 4.0,
        on_mean_min: float = 25.0,
        on_std_min: float = 10.0,
        off_hysteresis_s: float = 20.0,
        on_hysteresis_s: float = 1.5,
        freeze_diff_max: float = 1.5,
        freeze_hysteresis_s: float = 120.0,
        freeze_detection_enabled: bool = False,
    ) -> None:
        self.black_mean_max = black_mean_max
        self.black_std_max = black_std_max
        self.on_mean_min = on_mean_min
        self.on_std_min = on_std_min
        self.off_hysteresis_s = off_hysteresis_s
        self.on_hysteresis_s = on_hysteresis_s
        self.freeze_diff_max = freeze_diff_max
        self.freeze_hysteresis_s = freeze_hysteresis_s
        self.freeze_detection_enabled = freeze_detection_enabled
        self._first = True
        self._last_state = "off"
        self._off_since: float | None = None
        self._on_since: float | None = None
        self._static_since: float | None = None

    def reset(self) -> None:
        self._first = True
        self._last_state = "off"
        self._off_since = None
        self._on_since = None
        self._static_since = None

    def reset_static(self) -> None:
        self._static_since = None

    def _track_static(self, diff: float | None, now: float) -> bool:
        """Count unchanged frames only while the detector is confirmed ON."""
        if not self.freeze_detection_enabled:
            self._static_since = None
            return False
        if diff is not None and diff < self.freeze_diff_max:
            if self._static_since is None:
                self._static_since = now
        else:
            self._static_since = None
        return (
            self._static_since is not None
            and now - self._static_since >= self.freeze_hysteresis_s
        )

    def update(self, metrics: PowerMetrics, frame_diff: float | None, now: float) -> PowerReading:
        off_candidate = metrics.mean < self.black_mean_max and metrics.std < self.black_std_max
        on_candidate = metrics.mean > self.on_mean_min or metrics.std > self.on_std_min

        if self._first:
            self._first = False
            if off_candidate:
                self._last_state = "off"
                self._static_since = None
                return PowerReading("off", "initial black", False)
            if on_candidate:
                self._last_state = "on"
                self._off_since = None
                self._on_since = now
                self._static_since = now if self.freeze_detection_enabled else None
                return PowerReading("on", "initial signal", False)
            self._static_since = None
            return PowerReading(None, "initial ambiguous", False)

        if off_candidate:
            self._on_since = None
            self._static_since = None
            if self._off_since is None:
                self._off_since = now
            if self._last_state == "off":
                return PowerReading("off", "already off", False)
            if now - self._off_since >= self.off_hysteresis_s:
                self._last_state = "off"
                return PowerReading("off", "black hysteresis", False)
            return PowerReading(None, "off pending", False)

        if on_candidate:
            self._off_since = None
            if self._last_state == "on":
                frozen = self._track_static(frame_diff, now)
                if frozen:
                    self._last_state = "off"
                    self._on_since = None
                    self._static_since = None
                    return PowerReading("off", "frozen frame", True)
                return PowerReading("on", "already on", False)
            # Do not carry static time over from an OFF period.
            self._static_since = None
            if self._on_since is None:
                self._on_since = now
            if now - self._on_since >= self.on_hysteresis_s:
                self._last_state = "on"
                self._static_since = now if self.freeze_detection_enabled else None
                return PowerReading("on", "signal hysteresis", False)
            return PowerReading(None, "on pending", False)

        self._off_since = None
        self._static_since = None
        return PowerReading(None, "ambiguous", False)


def expand_region_width(region: Sequence[int], width: int = DEFAULT_BASE_WIDTH) -> list[int]:
    """Return the same vertical band stretched across the full frame width."""
    _x, y, _w, h = (int(v) for v in region)
    return [0, y, int(width), h]


def normalize_width(image: np.ndarray, target_width: int = DEFAULT_BASE_WIDTH) -> np.ndarray:
    height, width = image.shape[:2]
    if width == target_width or width == 0:
        return image
    scale = target_width / float(width)
    return cv2.resize(image, (target_width, int(round(height * scale))), interpolation=cv2.INTER_AREA)


def crop_region(
    image: np.ndarray,
    region: Sequence[int],
    base_width: int = DEFAULT_BASE_WIDTH,
) -> np.ndarray:
    """Crop a ``[x, y, w, h]`` region expressed relative to ``base_width``."""
    height, width = image.shape[:2]
    x, y, w, h = (float(v) for v in region)
    scale = width / float(base_width)
    x0 = max(0, int(round(x * scale)))
    y0 = max(0, int(round(y * scale)))
    x1 = min(width, int(round((x + w) * scale)))
    y1 = min(height, int(round((y + h) * scale)))
    if x1 <= x0 or y1 <= y0:
        return np.zeros((1, 1, 3), dtype=image.dtype)
    return image[y0:y1, x0:x1].copy()


def dock_score(
    image: np.ndarray,
    template: np.ndarray,
    region: Sequence[int],
    base_width: int = DEFAULT_BASE_WIDTH,
) -> float:
    """Normalized cross-correlation of the dock crop against a stored template."""
    crop = crop_region(image, region, base_width)
    if crop.size == 0 or template is None or template.size == 0:
        return 0.0
    search = to_gray(crop)
    tpl = to_gray(template)
    if tpl.shape != search.shape:
        tpl = cv2.resize(tpl, (search.shape[1], search.shape[0]), interpolation=cv2.INTER_AREA)
    if tpl.shape[0] > search.shape[0] or tpl.shape[1] > search.shape[1]:
        return 0.0
    result = cv2.matchTemplate(search, tpl, cv2.TM_CCOEFF_NORMED)
    value = float(np.nanmax(result))
    if np.isnan(value):
        return 0.0
    return max(0.0, min(1.0, value))


class HomeDetector:
    """Confirms HOME with a debounce window using template score and OCR labels."""

    def __init__(self, threshold: float = 0.80, debounce_s: float = 1.0, labels_required: int = 3) -> None:
        self.threshold = threshold
        self.debounce_s = debounce_s
        self.labels_required = labels_required
        self._candidate_since: float | None = None

    def reset(self) -> None:
        self._candidate_since = None

    def update(self, score: float, labels_found: int, now: float) -> bool:
        candidate = score >= self.threshold or labels_found >= self.labels_required
        if not candidate:
            self._candidate_since = None
            return False
        if self._candidate_since is None:
            self._candidate_since = now
        return now - self._candidate_since >= self.debounce_s


def preprocess_variants(
    image: np.ndarray,
    scale: int = 2,
    base_width: int = DEFAULT_BASE_WIDTH,
    region: Sequence[int] | None = None,
) -> list[np.ndarray]:
    """Return Otsu (normal + inverted) RGB variants ready for OCR."""
    crop = crop_region(image, region, base_width) if region else image
    gray = to_gray(crop)
    if scale > 1:
        gray = cv2.resize(
            gray,
            (gray.shape[1] * scale, gray.shape[0] * scale),
            interpolation=cv2.INTER_CUBIC,
        )
    enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    _threshold, normal = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    inverted = cv2.bitwise_not(normal)
    return [cv2.cvtColor(normal, cv2.COLOR_GRAY2RGB), cv2.cvtColor(inverted, cv2.COLOR_GRAY2RGB)]


def ocr_ready_crop(
    image: np.ndarray,
    region: Sequence[int] | None = None,
    scale: int = 2,
    base_width: int = DEFAULT_BASE_WIDTH,
) -> np.ndarray:
    """Grayscale, upscaled, contrast-enhanced RGB crop for OCR (no threshold)."""
    crop = crop_region(image, region, base_width) if region else image
    gray = to_gray(crop)
    if scale > 1:
        gray = cv2.resize(
            gray,
            (gray.shape[1] * scale, gray.shape[0] * scale),
            interpolation=cv2.INTER_CUBIC,
        )
    enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2RGB)


def resize_max_dimension(image: np.ndarray, max_dimension: int) -> np.ndarray:
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= max_dimension:
        return image
    scale = max_dimension / float(longest)
    return cv2.resize(image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
