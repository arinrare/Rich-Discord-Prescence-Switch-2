from __future__ import annotations

from pathlib import Path

from switch2_presence.config import (
    DEFAULT_CONFIG,
    discord_client_id,
    get,
    load_config,
    save_config,
)
from switch2_presence.winutil import dpapi_protect, dpapi_unprotect


def test_load_creates_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    cfg = load_config(path)
    assert path.exists()
    assert cfg["obs"]["source_name"] == "Elgato Neo"
    assert cfg["analysis"]["poll_ms"] == 1500
    assert cfg["discord"]["client_id"] == DEFAULT_CONFIG["discord"]["client_id"]


def test_load_deep_merges(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"analysis": {"poll_ms": 500}, "obs": {"source_name": "Capture"}}', encoding="utf-8")
    cfg = load_config(path)
    assert cfg["analysis"]["poll_ms"] == 500
    assert cfg["analysis"]["vision_width"] == 1280
    assert cfg["obs"]["source_name"] == "Capture"
    assert cfg["obs"]["port"] == 4455


def test_get_dotted() -> None:
    cfg = {"a": {"b": {"c": 3}}}
    assert get(cfg, "a.b.c") == 3
    assert get(cfg, "a.b.missing", "fallback") == "fallback"
    assert get(cfg, "missing", None) is None


def test_discord_client_id_placeholder() -> None:
    assert discord_client_id({"discord": {"client_id": "YOUR_DISCORD_CLIENT_ID"}}) == ""
    assert discord_client_id({"discord": {"client_id": ""}}) == ""
    assert discord_client_id({"discord": {"client_id": "123456789"}}) == "123456789"


def test_save_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    cfg = load_config(path)
    cfg["obs"]["port"] = 4460
    save_config(cfg, path)
    reloaded = load_config(path)
    assert reloaded["obs"]["port"] == 4460


def test_dpapi_round_trip() -> None:
    secret = "correct horse battery staple"
    encoded = dpapi_protect(secret)
    assert encoded != secret
    assert dpapi_unprotect(encoded) == secret
    assert dpapi_protect("") == ""
