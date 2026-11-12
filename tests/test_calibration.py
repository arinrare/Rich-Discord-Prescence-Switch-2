from __future__ import annotations

import numpy as np

from switch2_presence.calibration import (
    MIN_REGION_PX,
    _RegionSelector,
    clamp_region,
    normalize_region,
    region_is_valid,
)


def test_normalize_region_handles_reverse_drag() -> None:
    assert normalize_region(180, 90, 20, 10) == (20, 10, 160, 80)
    assert normalize_region(5, 6, 5, 6) == (5, 6, 0, 0)


def test_clamp_region_to_frame() -> None:
    assert clamp_region([-10, -5, 100, 100], 50, 40) == [0, 0, 50, 40]
    assert clamp_region([45, 35, 100, 100], 50, 40) == [45, 35, 5, 5]


def test_region_is_valid() -> None:
    assert region_is_valid([0, 0, 100, 80], 200, 100)
    assert not region_is_valid([0, 0, MIN_REGION_PX - 1, 80], 200, 100)
    assert not region_is_valid([0, 0, 250, 80], 200, 100)
    assert not region_is_valid([-1, 0, 100, 80], 200, 100)


def test_selector_region_from_reverse_drag() -> None:
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    selector = _RegionSelector(frame, ["test"])
    selector.start = (180, 90)
    selector.end = (20, 10)
    region = selector._region()
    assert region == [20, 10, 160, 80]
    assert region_is_valid(region, 200, 100)


def test_selector_click_without_drag_is_invalid() -> None:
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    selector = _RegionSelector(frame, ["test"])
    selector.start = (50, 50)
    selector.end = (50, 50)
    assert not region_is_valid(selector._region(), 200, 100)


def test_selection_draws_double_outline_on_dark_frame() -> None:
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    selector = _RegionSelector(frame, ["test"])
    selector.start = (20, 20)
    selector.end = (100, 60)
    canvas = selector._draw_selection(frame.copy(), header_height=0)
    # White inner outline and dark outer ring must both exist around the box.
    assert canvas[20, 60].tolist() == [255, 255, 255]
    assert canvas[18, 60].tolist() == [0, 0, 0]
    # Area outside the box is dimmed but the selected content is not.
    assert canvas[80, 180].tolist() == [0, 0, 0]
