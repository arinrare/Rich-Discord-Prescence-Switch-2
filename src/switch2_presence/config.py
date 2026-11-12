"""Configuration loading, defaults, and atomic persistence."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any

APP_NAME = "Switch2DiscordPresence"
ENV_DATA_HOME = "SWITCH2_PRESENCE_HOME"
ENV_OBS_PASSWORD = "SWITCH2_OBS_PASSWORD"

PLACEHOLDER_CLIENT_ID = "YOUR_DISCORD_CLIENT_ID"

DEFAULT_CONFIG: dict[str, Any] = {
    "obs": {
        "host": "127.0.0.1",
        "port": 4455,
        "password_dpapi": "",
        "source_name": "Elgato Neo",
    },
    "discord": {
        "client_id": PLACEHOLDER_CLIENT_ID,
        "large_image": "switch2",
        "small_image": "switch2",
    },
    "analysis": {
        "poll_ms": 1500,
        "vision_poll_ms": 2000,
        "vision_width": 1280,
        "black_mean_max": 12,
        "black_std_max": 4,
        "on_mean_min": 25,
        "on_std_min": 10,
        "off_hysteresis_s": 20,
        "on_hysteresis_s": 1.5,
        "freeze_diff_max": 1.5,
        "freeze_hysteresis_s": 120,
        "freeze_detection_enabled": False,
        "home_match_threshold": 0.80,
        "home_debounce_s": 1.0,
        "video_grace_s": 3.0,
        "title_change_threshold": 6.0,
    },
    "regions": {
        "title": [760, 780, 400, 60],
        "icon": [850, 480, 220, 220],
        "dock": [420, 1000, 1080, 80],
    },
    "ocr": {
        "engine": "windows",
        "language": "en-US",
        "scale": 2,
        "fuzzy_cutoff": 80,
        "dock_fallback_labels": 3,
        "full_width_title": True,
        "refresh_s": 10,
    },
    "timer": {
        "freeze_on_home": True,
        "freeze_on_sleep": True,
        "clear_presence_after_off_s": 60,
    },
    "handheld": {
        "enabled": False,
        "game": None,
        "generic": False,
    },
    "ui": {
        "start_minimized": True,
        "notify_new_games": True,
    },
    "discord_min_interval_s": 15,
}


def app_data_dir() -> Path:
    override = os.environ.get(ENV_DATA_HOME)
    if override:
        return Path(override)
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ("." + APP_NAME)


def config_path() -> Path:
    return app_data_dir() / "config.json"


def calibration_path() -> Path:
    return app_data_dir() / "calibration.json"


def games_path() -> Path:
    return app_data_dir() / "games.json"


def state_path() -> Path:
    return app_data_dir() / "state.json"


def dock_template_path() -> Path:
    return app_data_dir() / "dock_template.png"


def logs_dir() -> Path:
    return app_data_dir() / "logs"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def default_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_CONFIG)


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    path = Path(path) if path else config_path()
    if not path.exists():
        cfg = default_config()
        save_config(cfg, path)
        return cfg
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        cfg = default_config()
        save_config(cfg, path)
        return cfg
    return _deep_merge(DEFAULT_CONFIG, raw if isinstance(raw, dict) else {})


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def save_config(cfg: dict[str, Any], path: Path | str | None = None) -> None:
    atomic_write_json(Path(path) if path else config_path(), cfg)


def get(cfg: dict[str, Any], dotted: str, default: Any = None) -> Any:
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def discord_client_id(cfg: dict[str, Any]) -> str:
    value = str(get(cfg, "discord.client_id", "") or "").strip()
    if not value or value == PLACEHOLDER_CLIENT_ID or "YOUR_" in value.upper():
        return ""
    return value


def obs_password(cfg: dict[str, Any]) -> str:
    env = os.environ.get(ENV_OBS_PASSWORD)
    if env:
        return env
    blob = str(get(cfg, "obs.password_dpapi", "") or "").strip()
    if not blob:
        return ""
    from .winutil import dpapi_unprotect

    try:
        return dpapi_unprotect(blob)
    except Exception:
        return ""
