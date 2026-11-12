from __future__ import annotations

import numpy as np

from switch2_presence.vision import (
    HomeDetector,
    PowerDetector,
    crop_region,
    diff_score,
    dock_score,
    expand_region_width,
    normalize_width,
    power_metrics,
    preprocess_variants,
)


def make_frame(value: int, noise: int = 0, width: int = 320, height: int = 180) -> np.ndarray:
    rng = np.random.default_rng(value * 131 + noise * 17 + width + height)
    image = np.full((height, width, 3), value, dtype=np.int16)
    if noise:
        image += rng.normal(0, noise, image.shape).astype(np.int16)
    return np.clip(image, 0, 255).astype(np.uint8)


def test_power_metrics_black_and_bright() -> None:
    black = power_metrics(make_frame(3))
    bright = power_metrics(make_frame(140, noise=30))
    assert black.mean < 12 and black.std < 4
    assert bright.mean > 25 or bright.std > 10


def test_power_hysteresis_off() -> None:
    detector = PowerDetector(off_hysteresis_s=20, on_hysteresis_s=1.5)
    bright = make_frame(140, noise=30)
    assert detector.update(power_metrics(bright), 10.0, 0.0).state == "on"

    black = make_frame(2)
    reading = detector.update(power_metrics(black), 50.0, 10.0)
    assert reading.state is None
    reading = detector.update(power_metrics(black), 50.0, 31.0)
    assert reading.state == "off"


def test_power_hysteresis_on() -> None:
    detector = PowerDetector(on_hysteresis_s=1.5)
    black = make_frame(2)
    assert detector.update(power_metrics(black), 50.0, 0.0).state == "off"
    bright = make_frame(140, noise=30)
    assert detector.update(power_metrics(bright), 10.0, 1.0).state is None
    assert detector.update(power_metrics(bright), 10.0, 3.0).state == "on"


def test_frozen_frame_fallback() -> None:
    detector = PowerDetector(
        freeze_diff_max=1.5,
        freeze_hysteresis_s=120,
        freeze_detection_enabled=True,
    )
    bright = make_frame(140, noise=30)
    assert detector.update(power_metrics(bright), 0.0, 0.0).state == "on"
    assert detector.update(power_metrics(bright), 0.0, 60.0).state == "on"
    reading = detector.update(power_metrics(bright), 0.0, 121.0)
    assert reading.state == "off"
    assert reading.reason == "frozen frame"


def test_frozen_frame_is_ignored_when_disabled() -> None:
    detector = PowerDetector(freeze_diff_max=1.5, freeze_hysteresis_s=120)
    bright = make_frame(140, noise=30)
    assert detector.update(power_metrics(bright), 0.0, 0.0).state == "on"
    assert detector.update(power_metrics(bright), 0.0, 121.0).state == "on"
    assert detector.update(power_metrics(bright), 0.0, 500.0).state == "on"


def test_frozen_timer_starts_after_on_transition() -> None:
    detector = PowerDetector(
        freeze_diff_max=1.5,
        freeze_hysteresis_s=120,
        freeze_detection_enabled=True,
        off_hysteresis_s=20,
    )
    black = make_frame(2)
    bright = make_frame(140, noise=30)
    assert detector.update(power_metrics(black), 50.0, 0.0).state == "off"
    # Long static OFF period must not leak into the next ON session.
    assert detector.update(power_metrics(black), 0.0, 200.0).state == "off"
    assert detector.update(power_metrics(bright), 0.0, 210.0).state is None
    assert detector.update(power_metrics(bright), 0.0, 212.0).state == "on"
    assert detector.update(power_metrics(bright), 0.0, 300.0).state == "on"
    reading = detector.update(power_metrics(bright), 0.0, 333.0)
    assert reading.state == "off"
    assert reading.reason == "frozen frame"


def test_expand_region_width() -> None:
    assert expand_region_width([19, 207, 684, 83]) == [0, 207, 1920, 83]


def test_crop_region_scales_with_width() -> None:
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    crop = crop_region(frame, [960, 540, 96, 54])
    assert crop.shape[:2] == (54, 96)

    half = normalize_width(frame, 960)
    assert half.shape == (540, 960, 3)
    crop_half = crop_region(half, [960, 540, 96, 54])
    assert crop_half.shape[:2] == (27, 48)


def test_diff_score() -> None:
    a = make_frame(10)
    b = make_frame(10)
    assert diff_score(a, b) == 0.0
    c = make_frame(200)
    assert diff_score(a, c) > 10


def test_dock_score_identical_and_different() -> None:
    frame = make_frame(120, noise=20, width=1920, height=1080)
    region = [420, 1000, 1080, 80]
    template = crop_region(frame, region)
    assert dock_score(frame, template, region) > 0.99
    other = make_frame(30, noise=40, width=1920, height=1080)
    assert dock_score(other, template, region) < 0.5


def test_home_detector_debounce() -> None:
    detector = HomeDetector(threshold=0.8, debounce_s=1.0, labels_required=3)
    assert detector.update(0.1, 0, 0.0) is False
    assert detector.update(0.9, 0, 1.0) is False
    assert detector.update(0.9, 0, 1.5) is False
    assert detector.update(0.9, 0, 2.1) is True
    assert detector.update(0.2, 0, 3.0) is False

    detector.reset()
    assert detector.update(0.0, 3, 10.0) is False
    assert detector.update(0.0, 3, 11.2) is True


def test_preprocess_variants_shape() -> None:
    frame = make_frame(90, noise=10, width=400, height=60)
    variants = preprocess_variants(frame, scale=2, region=[0, 0, 400, 60], base_width=400)
    assert len(variants) == 2
    for variant in variants:
        assert variant.shape == (120, 800, 3)
        assert variant.dtype == np.uint8
