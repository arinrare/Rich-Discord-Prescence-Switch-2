"""Command-line entry point."""

from __future__ import annotations

import argparse
import getpass
import sys
import time
from pathlib import Path

from . import __version__
from .app import App
from .config import (
    config_path,
    discord_client_id,
    dock_template_path,
    games_path,
    get,
    load_config,
    logs_dir,
    obs_password,
    save_config,
)
from .log import setup_logging
from .obs import OBSClient, OBSUnavailable
from .win_ocr import create_engine
from .winutil import SingleInstance, dpapi_protect


def _set_obs_password(config_file: Path) -> int:
    cfg = load_config(config_file)
    password = getpass.getpass("OBS WebSocket password (leave blank to clear): ")
    cfg.setdefault("obs", {})["password_dpapi"] = dpapi_protect(password) if password else ""
    save_config(cfg, config_file)
    print("Saved OBS password with Windows DPAPI." if password else "Cleared OBS password.")
    return 0


def _set_discord_client_id(config_file: Path) -> int:
    cfg = load_config(config_file)
    current = discord_client_id(cfg)
    prompt = f"Discord application client ID [{current or 'not set'}]: "
    value = input(prompt).strip()
    if not value:
        print("Unchanged.")
        return 0
    cfg.setdefault("discord", {})["client_id"] = value
    save_config(cfg, config_file)
    print("Saved Discord client ID.")
    return 0


def _diagnose(config_file: Path) -> int:
    logger = setup_logging(logs_dir(), console=True)
    cfg = load_config(config_file)
    print(f"config:      {config_file}")
    print(f"data dir:    {config_file.parent}")
    print(f"games:       {games_path()}")
    print(f"logs:        {logs_dir()}")
    print(f"dock tpl:    {dock_template_path()} ({'present' if dock_template_path().exists() else 'missing'})")
    print(f"source:      {get(cfg, 'obs.source_name')}")
    password = obs_password(cfg)
    print(f"OBS password: {'configured' if password else 'not configured'}")
    client_id = discord_client_id(cfg)
    print(f"Discord app:  {'configured (' + client_id[:6] + '...)' if client_id else 'NOT configured'}")

    engine = create_engine(str(get(cfg, "ocr.engine", "windows")), str(get(cfg, "ocr.language", "en-US")))
    try:
        print(f"OCR engine:   {'available' if engine.available else 'unavailable'}")
    except Exception as exc:
        print(f"OCR engine:   unavailable ({exc})")

    if not password:
        print("note: set a password with --set-obs-password if OBS requires auth.")

    client = OBSClient(
        host=str(get(cfg, "obs.host", "127.0.0.1")),
        port=int(get(cfg, "obs.port", 4455)),
        password=password,
        source_name=str(get(cfg, "obs.source_name", "Elgato Neo")),
        logger=logger,
    )
    try:
        client.connect()
        print(f"OBS:          connected (obs-websocket {client.version})")
        print(f"OBS scene:    {client.scene_name()}")
        print(f"Capture srcs: {', '.join(client.list_capture_sources()) or '(none found)'}")
        for index in range(3):
            _image, mean, std, edges = client.probe()
            print(f"sample {index + 1}:     mean={mean:6.2f} std={std:6.2f} edges={edges:8.1f}")
            time.sleep(0.4)
        print("hint: run the app and choose 'Run calibration...' from the tray.")
    except OBSUnavailable as exc:
        print(f"OBS:          unavailable ({exc})")
        print("hint: enable Tools -> WebSocket Server Settings in OBS and match the port/password.")
        return 2
    finally:
        client.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="switch2-presence", description="Switch 2 -> Discord Rich Presence")
    parser.add_argument("--config", type=Path, default=None, help="path to config.json")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--set-obs-password",
        action="store_true",
        help="prompt and store the OBS WebSocket password (DPAPI)",
    )
    parser.add_argument(
        "--set-discord-client-id",
        action="store_true",
        help="prompt and store the Discord application client ID",
    )
    parser.add_argument("--diagnose", action="store_true", help="check config, OCR, and OBS connectivity then exit")
    parser.add_argument("--headless", action="store_true", help="run detection without the tray (debug)")
    args = parser.parse_args(argv)

    config_file = args.config or config_path()

    if args.set_obs_password:
        return _set_obs_password(config_file)
    if args.set_discord_client_id:
        return _set_discord_client_id(config_file)
    if args.diagnose:
        return _diagnose(config_file)

    with SingleInstance() as instance:
        if instance.already_running:
            print("Switch2DiscordPresence is already running.", file=sys.stderr)
            return 1
        app = App(config_file, headless=args.headless)
        return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
