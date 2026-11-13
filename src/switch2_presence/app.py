"""Application orchestration: OBS polling, vision, state, presence, tray."""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Any

import cv2

from . import calibration
from .config import (
    atomic_write_json,
    config_path,
    discord_client_id,
    dock_template_path,
    games_path,
    get,
    load_config,
    logs_dir,
    obs_password,
    save_config,
    state_path,
)
from .games import GameDB, is_system_app
from .handheld import HandheldAction
from .handheld import apply as apply_handheld
from .log import setup_logging
from .obs import OBSClient, OBSUnavailable
from .ocr import TitleReader
from .presence import PresenceManager, build_payload
from .state import GameState, PresenceMode, StateMachine
from .tray import Tray, TrayStatus
from .vision import (
    HomeDetector,
    PowerDetector,
    crop_region,
    diff_score,
    dock_score,
    expand_region_width,
    power_metrics,
)
from .win_ocr import OcrUnavailable
from .winutil import open_in_explorer

POWER_SIZE = (320, 180)


class App:
    def __init__(self, config_file: Path | str | None = None, headless: bool = False) -> None:
        self.config_file = Path(config_file) if config_file else config_path()
        self.log = setup_logging(logs_dir())
        self.cfg = load_config(self.config_file)
        self.headless = headless
        self.games = GameDB(games_path(), cutoff=int(get(self.cfg, "ocr.fuzzy_cutoff", 80)))
        self.machine = StateMachine(
            freeze_on_home=bool(get(self.cfg, "timer.freeze_on_home", True)),
            freeze_on_sleep=bool(get(self.cfg, "timer.freeze_on_sleep", True)),
        )
        self.obs = OBSClient(
            host=str(get(self.cfg, "obs.host", "127.0.0.1")),
            port=int(get(self.cfg, "obs.port", 4455)),
            password=obs_password(self.cfg),
            source_name=str(get(self.cfg, "obs.source_name", "Elgato Neo")),
            logger=self.log,
        )
        self.reader = TitleReader(
            self.games,
            language=str(get(self.cfg, "ocr.language", "en-US")),
            scale=int(get(self.cfg, "ocr.scale", 2)),
            cutoff=int(get(self.cfg, "ocr.fuzzy_cutoff", 80)),
            engine_kind=str(get(self.cfg, "ocr.engine", "windows")),
            dock_labels_required=int(get(self.cfg, "ocr.dock_fallback_labels", 3)),
        )
        self.presence = PresenceManager(
            discord_client_id(self.cfg),
            large_image=str(get(self.cfg, "discord.large_image", "switch2")),
            small_image=str(get(self.cfg, "discord.small_image", "")),
            min_interval=float(get(self.cfg, "discord_min_interval_s", 15)),
            logger=self.log,
        )
        self.commands: queue.Queue[tuple[str, tuple[Any, ...]]] = queue.Queue()
        self.ui_commands: queue.Queue[tuple[str, tuple[Any, ...]]] = queue.Queue()
        self._ui_busy = threading.Event()
        self.stop_event = threading.Event()
        self.paused = False
        self._signal_on = False
        self._obs_connected = False
        self._obs_error: str | None = None
        self._ocr_error: str | None = None
        self._tray: Tray | None = None
        self._tray_status = TrayStatus()
        self._power = self._build_power_detector()
        self._home = HomeDetector(
            threshold=float(get(self.cfg, "analysis.home_match_threshold", 0.80)),
            debounce_s=float(get(self.cfg, "analysis.home_debounce_s", 1.0)),
            labels_required=int(get(self.cfg, "ocr.dock_fallback_labels", 3)),
        )
        self._dock_template = self._load_dock_template()
        self._prev_frame = None
        self._next_power_at = 0.0
        self._next_vision_at = 0.0
        self._video_since: float | None = None
        self._last_ocr_at = 0.0
        self._last_title_crop = None
        self._last_reading = None
        self._pending_title: str | None = None
        self._pending_title_count = 0
        self._pending_clear_at: float | None = None
        self._session_game: str | None = None
        self._session_mark = 0.0
        self._next_flush_at = 0.0
        self._next_save_at = 0.0
        self._last_mode: PresenceMode | None = None
        self._restore_state()
        self._pending_clear_at = time.monotonic()

    # ---- setup helpers ---------------------------------------------------
    def _build_power_detector(self) -> PowerDetector:
        return PowerDetector(
            black_mean_max=float(get(self.cfg, "analysis.black_mean_max", 12)),
            black_std_max=float(get(self.cfg, "analysis.black_std_max", 4)),
            on_mean_min=float(get(self.cfg, "analysis.on_mean_min", 25)),
            on_std_min=float(get(self.cfg, "analysis.on_std_min", 10)),
            off_hysteresis_s=float(get(self.cfg, "analysis.off_hysteresis_s", 20)),
            on_hysteresis_s=float(get(self.cfg, "analysis.on_hysteresis_s", 1.5)),
            freeze_diff_max=float(get(self.cfg, "analysis.freeze_diff_max", 1.5)),
            freeze_hysteresis_s=float(get(self.cfg, "analysis.freeze_hysteresis_s", 120)),
            freeze_detection_enabled=bool(
                get(self.cfg, "analysis.freeze_detection_enabled", False)
            ),
        )

    def _load_dock_template(self):
        path = dock_template_path()
        if not path.exists():
            return None
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            self.log.warning("Dock template exists but could not be read: %s", path)
        return image

    def _restore_state(self) -> None:
        path = state_path()
        if not path.exists():
            return
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        self.machine.restore(data, time.monotonic())
        if self.machine.last_game:
            self.log.info("Restored last game: %s", self.machine.last_game)
        self._pending_clear_at = time.monotonic()

    # ---- entry point -----------------------------------------------------
    def run(self) -> int:
        try:
            if not self.reader.available:
                self.log.warning("OCR engine is not available; HOME titles cannot be read")
        except Exception as exc:
            self.log.warning("OCR availability check failed: %s", exc)

        if self.headless:
            self.log.info("Running headless (no tray)")
            try:
                self._worker()
            finally:
                self.shutdown()
            return 0
        self._tray = Tray(self._status(), self.enqueue, self.log)
        self._tray.start_detached()
        worker = threading.Thread(target=self._worker, name="detector", daemon=True)
        worker.start()
        try:
            self._main_loop()
        finally:
            self.stop_event.set()
            self._tray.stop()
            worker.join(timeout=5)
        self.shutdown()
        return 0

    def _main_loop(self) -> None:
        """Runs UI work (calibration) on the main thread; tray runs detached."""
        while not self.stop_event.is_set():
            try:
                name, _args = self.ui_commands.get(timeout=0.2)
            except queue.Empty:
                continue
            if name == "quit":
                self.request_quit()
                return
            if name == "calibrate":
                self._ui_busy.set()
                try:
                    self._run_calibration()
                except Exception as exc:
                    self.log.exception("calibration failed: %s", exc)
                    if self._tray:
                        self._tray.notify(f"Calibration failed: {exc}")
                finally:
                    self._ui_busy.clear()

    def request_quit(self) -> None:
        self.stop_event.set()
        if self._tray:
            self._tray.stop()

    def enqueue(self, name: str, *args: object) -> None:
        if name == "quit":
            self.request_quit()
            return
        if name in ("calibrate",):
            self.ui_commands.put((name, args))
            return
        self.commands.put((name, args))

    # ---- worker ----------------------------------------------------------
    def _worker(self) -> None:
        self.log.info("Detector started (source=%s)", self.obs.source_name)
        while not self.stop_event.is_set():
            now = time.monotonic()
            self._drain_commands(now)
            if not self.paused and not self._ui_busy.is_set():
                if now >= self._next_power_at:
                    self._poll_power(now)
                    self._next_power_at = now + float(get(self.cfg, "analysis.poll_ms", 1500)) / 1000.0
                if now >= self._next_vision_at and self._should_run_vision():
                    self._poll_vision(now)
                    self._next_vision_at = now + float(get(self.cfg, "analysis.vision_poll_ms", 2000)) / 1000.0
            self.presence.tick()
            self._update_presence(now)
            self._sync_session(now)
            self._maybe_flush(now)
            self._maybe_save(now)
            self._refresh_tray(now)
            self.stop_event.wait(0.2)

    def _should_run_vision(self) -> bool:
        return self._signal_on and self.machine.state in (
            GameState.UNKNOWN,
            GameState.HOME,
            GameState.IN_GAME,
        )

    def _drain_commands(self, now: float) -> None:
        while True:
            try:
                name, args = self.commands.get_nowait()
            except queue.Empty:
                return
            try:
                self._handle_command(name, args, now)
            except Exception as exc:
                self.log.exception("command %s failed: %s", name, exc)

    def _handle_command(self, name: str, args: tuple[Any, ...], now: float) -> None:
        if name == "quit":
            self.request_quit()
            return
        if name == "toggle_pause":
            self.paused = not self.paused
            self.log.info("Detection %s", "paused" if self.paused else "resumed")
            if not self.paused:
                self._power.reset()
                self._next_power_at = now
                self._next_vision_at = now
            return
        if name == "open_logs":
            open_in_explorer(logs_dir())
            return
        if name == "calibrate":
            self.ui_commands.put((name, args))
            return
        if name == "handheld_enable":
            if self.machine.state is GameState.HANDHELD:
                self._leave_handheld(now)
            else:
                self.machine.enter_handheld(now)
                self.log.info("Handheld mode enabled")
            self._after_manual(now)
            return
        if name == "handheld_disable":
            self._leave_handheld(now)
            self._after_manual(now)
            return
        if name == "choose_game":
            apply_handheld(self.machine, self.games, HandheldAction.CHOOSE_GAME, now, game=str(args[0]))
            self._after_manual(now)
            return
        if name == "handheld_resume":
            apply_handheld(self.machine, self.games, HandheldAction.RESUME_LAST, now)
            self._after_manual(now)
            return
        if name == "handheld_generic":
            apply_handheld(self.machine, self.games, HandheldAction.GENERIC, now)
            self._after_manual(now)
            return
        if name == "handheld_clear":
            apply_handheld(self.machine, self.games, HandheldAction.CLEAR, now)
            self.presence.clear(force=True)
            self._after_manual(now)
            return
        self.log.debug("unknown command %s", name)

    def _leave_handheld(self, now: float) -> None:
        self.machine.leave_handheld(now, signal_on=self._signal_on)
        self._home.reset()
        self._video_since = None
        self._pending_clear_at = None
        self._next_vision_at = now
        self.log.info("Automatic mode restored")

    def _after_manual(self, now: float) -> None:
        self._sync_session(now)
        self._update_presence(now, force=True)
        self._save_state(now)
        self._refresh_tray(now)

    # ---- power -----------------------------------------------------------
    def _poll_power(self, now: float) -> None:
        try:
            frame = self.obs.screenshot(*POWER_SIZE)
        except OBSUnavailable as exc:
            if self._obs_connected or self._obs_error != str(exc):
                self.log.warning("OBS unavailable: %s", exc)
            self._obs_connected = False
            self._obs_error = str(exc)
            return
        if not self._obs_connected:
            self.log.info("OBS screenshot polling active")
        self._obs_connected = True
        self._obs_error = None

        diff = diff_score(self._prev_frame, frame) if self._prev_frame is not None else None
        self._prev_frame = frame
        reading = self._power.update(power_metrics(frame), diff, now)
        self._signal_on = reading.state == "on"
        if reading.state == "off":
            self._on_off(now, reading.reason)
        elif reading.state == "on":
            self._on_signal_on(now, reading.reason)

    def _on_signal_on(self, now: float, reason: str) -> None:
        if self.machine.state is GameState.HANDHELD:
            return
        if self.machine.state is not GameState.OFF:
            self._update_presence(now)
            return
        self.machine.on_signal_on(now)
        self._power.reset_static()
        self._pending_clear_at = None
        self._video_since = None
        self._home.reset()
        self._pending_title = None
        self._pending_title_count = 0
        self._next_vision_at = now
        self.log.info("Signal ON (%s)", reason)
        self.log.info("State -> UNKNOWN")
        self._update_presence(now)

    def _on_off(self, now: float, reason: str) -> None:
        if self.machine.state is GameState.HANDHELD:
            return
        if self.machine.state is GameState.OFF:
            return
        self.machine.on_signal_off(now)
        self._sync_session(now)
        self._video_since = None
        self._home.reset()
        self._pending_title = None
        self._pending_title_count = 0
        grace = float(get(self.cfg, "timer.clear_presence_after_off_s", 60))
        self._pending_clear_at = now + max(0.0, grace)
        self.log.info("State -> OFF (%s)", reason)
        if grace <= 0:
            self.presence.clear()
        self._save_state(now)
        self._refresh_tray(now)

    # ---- vision ----------------------------------------------------------
    def _vision_frame(self):
        width = int(get(self.cfg, "analysis.vision_width", 1280))
        height = int(round(width * 9 / 16))
        return self.obs.screenshot(width, height)

    def _poll_vision(self, now: float) -> None:
        try:
            frame = self._vision_frame()
        except OBSUnavailable as exc:
            self.log.debug("vision capture failed: %s", exc)
            self._obs_connected = False
            self._obs_error = str(exc)
            return
        self._obs_connected = True
        self._obs_error = None

        score = 0.0
        labels = 0
        dock_region = get(self.cfg, "regions.dock")
        threshold = float(get(self.cfg, "analysis.home_match_threshold", 0.80))
        if self._dock_template is not None and dock_region:
            try:
                score = dock_score(frame, self._dock_template, dock_region)
            except Exception as exc:
                self.log.debug("dock score failed: %s", exc)
        if dock_region and score < threshold and self.reader.available:
            try:
                labels, _ = self.reader.dock_labels(frame, dock_region)
            except OcrUnavailable as exc:
                self._disable_ocr(exc)
            except Exception as exc:
                self.log.debug("dock OCR failed: %s", exc)
                self._disable_ocr(exc)

        home_now = self._home.update(score, labels, now)
        if home_now:
            self._video_since = None
            self._handle_home(now, frame)
        else:
            if self.machine.state is GameState.HOME:
                self._handle_video(now)
            elif self.machine.state is GameState.UNKNOWN:
                if self._video_since is None:
                    self._video_since = now
                grace = float(get(self.cfg, "analysis.video_grace_s", 3.0))
                if now - self._video_since >= grace:
                    self._handle_video(now)
        self._update_presence(now)

    def _disable_ocr(self, exc: BaseException) -> None:
        message = str(exc) or exc.__class__.__name__
        if self._ocr_error != message:
            self.log.warning("OCR disabled: %s", message)
        self._ocr_error = message
        self.reader.disable(message)

    def _title_region(self) -> list[int] | None:
        region = get(self.cfg, "regions.title")
        if not region:
            return None
        if bool(get(self.cfg, "ocr.full_width_title", True)):
            return expand_region_width(region)
        return [int(v) for v in region]

    def _read_title(self, now: float, frame) -> str | None:
        region = self._title_region()
        if not region or not self.reader.available:
            return None
        try:
            crop = crop_region(frame, region)
        except Exception:
            crop = None
        changed = True
        if crop is not None and self._last_title_crop is not None:
            changed = diff_score(crop, self._last_title_crop) >= float(
                get(self.cfg, "analysis.title_change_threshold", 6.0)
            )
        if not changed and now - self._last_ocr_at < float(get(self.cfg, "ocr.refresh_s", 10)):
            return self.machine.selected_title
        if crop is not None:
            self._last_title_crop = crop
        self._last_ocr_at = now
        try:
            reading = self.reader.read_title(frame, region)
        except OcrUnavailable as exc:
            self._disable_ocr(exc)
            return None
        except Exception as exc:
            self.log.debug("title OCR failed: %s", exc)
            return None
        self._last_reading = reading
        if reading.is_system:
            self._pending_title = None
            self._pending_title_count = 0
            if reading.clean != self.machine.selected_title:
                self.log.info("OCR system app: %r", reading.clean)
            else:
                self.log.debug("OCR system app unchanged: %r", reading.clean)
            return reading.clean
        if reading.match is not None:
            canonical = reading.match.canonical
            self._pending_title = None
            self._pending_title_count = 0
            self.games.touch(canonical)
            if canonical != self.machine.selected_title:
                self.log.info(
                    "OCR title: %r -> %s (%.0f)", reading.clean, canonical, reading.match.score
                )
            else:
                self.log.debug("OCR title unchanged: %r", canonical)
            return canonical
        if reading.usable and self._plausible_provisional(reading.clean):
            if reading.clean.casefold() == (self._pending_title or "").casefold():
                self._pending_title_count += 1
                self.log.debug("OCR provisional title repeat: %r", reading.clean)
            else:
                self._pending_title = reading.clean
                self._pending_title_count = 1
                self.log.info("OCR provisional title: %r (awaiting confirmation)", reading.clean)
            if self._pending_title_count >= 2:
                entry, created = self.games.add(reading.clean)
                self._pending_title = None
                self._pending_title_count = 0
                if created:
                    self.log.info("New game title added: %s", entry.canonical)
                    if self._tray and bool(get(self.cfg, "ui.notify_new_games", True)):
                        self._tray.notify(f"New game detected: {entry.canonical}")
                return entry.canonical
            return reading.clean
        self._pending_title = None
        self._pending_title_count = 0
        if reading.clean:
            self.log.debug("OCR text ignored: %r", reading.clean)
        return None

    @staticmethod
    def _plausible_provisional(text: str) -> bool:
        stripped = text.strip()
        if not stripped or stripped[-1] in ".,;:":
            return False
        alnum = sum(ch.isalnum() for ch in stripped)
        return alnum >= 4

    def _handle_home(self, now: float, frame) -> None:
        title = self._read_title(now, frame)
        state_before = self.machine.state
        mode_before = self.machine.presence_mode()
        title_before = self.machine.selected_title
        self.machine.on_home(
            now,
            selected_title=title,
            selected_is_system=is_system_app(title) if title else False,
        )
        mode_after = self.machine.presence_mode()
        if (
            state_before is not GameState.HOME
            or mode_before is not mode_after
            or self.machine.selected_title != title_before
        ):
            self.log.info(
                "State -> HOME%s",
                f" ({self.machine.selected_title})" if self.machine.selected_title else "",
            )
            self._update_presence(now)
            self._save_state(now)

    def _handle_video(self, now: float) -> None:
        before_game = self.machine.current_game
        self.machine.on_video(now)
        if self.machine.current_game != before_game:
            self.log.info("Game: %s", self.machine.current_game or "(none)")
        self.log.info("State -> %s", self.machine.state.value)
        if self.machine.current_game:
            game = self.machine.current_game
            if self.games.get(game) is None:
                entry, created = self.games.add(game, save=True)
                if created:
                    self.log.info("New game title added on launch: %s", entry.canonical)
                    if self._tray and bool(get(self.cfg, "ui.notify_new_games", True)):
                        self._tray.notify(f"New game detected: {entry.canonical}")
            else:
                self.games.touch(game)
        self._pending_title = None
        self._pending_title_count = 0
        self._sync_session(now)
        self._update_presence(now)
        self._save_state(now)
        self._refresh_tray(now)

    # ---- presence --------------------------------------------------------
    def _update_presence(self, now: float, force: bool = False) -> None:
        mode = self.machine.presence_mode()
        if mode is PresenceMode.OFF:
            if self.machine.state is GameState.OFF and not self.machine.no_presence:
                if self._pending_clear_at is None:
                    return
                if now < self._pending_clear_at:
                    return
                self.presence.set(None)
                self._pending_clear_at = None
            elif force:
                self.presence.set(None, now, force=True)
            if mode is not self._last_mode:
                self.log.info("Presence -> %s", mode.value)
                self._last_mode = mode
            return

        active = self.machine.active_seconds(now)
        start_ts = int(time.time() - active) if (self.machine.timer_running or active > 0) else None
        payload = build_payload(
            mode,
            self.machine.presence_game(),
            start_ts,
            large_image=str(get(self.cfg, "discord.large_image", "switch2")),
            small_image=str(get(self.cfg, "discord.small_image", "")),
            home_selected=self.machine.selected_title,
        )
        self.presence.set(payload, now, force=force)
        if mode is not self._last_mode:
            self.log.info(
                "Presence -> %s%s",
                mode.value,
                f" [{self.machine.presence_game()}]" if self.machine.presence_game() else "",
            )
            self._last_mode = mode

    # ---- sessions / persistence -----------------------------------------
    def _sync_session(self, now: float) -> None:
        mode = self.machine.presence_mode()
        current = self.machine.current_game if mode in (
            PresenceMode.IN_GAME,
            PresenceMode.HANDHELD_GAME,
        ) else None
        if current == self._session_game:
            return
        if self._session_game:
            self._flush_session(now)
        self._session_game = current
        self._session_mark = self.machine.active_seconds(now) if current else 0.0

    def _flush_session(self, now: float) -> None:
        if not self._session_game:
            return
        active = self.machine.active_seconds(now)
        delta = active - self._session_mark
        if delta >= 1.0:
            self.games.record_seconds(self._session_game, delta, save=False)
            self._session_mark = active

    def _maybe_flush(self, now: float) -> None:
        if now < self._next_flush_at:
            return
        self._next_flush_at = now + 60.0
        if self._session_game:
            self._flush_session(now)
            self.games.save()

    def _maybe_save(self, now: float) -> None:
        if now < self._next_save_at:
            return
        self._next_save_at = now + 30.0
        self._save_state(now)

    def _save_state(self, now: float) -> None:
        snapshot = self.machine.snapshot(now)
        payload = snapshot.to_json(time.time())
        payload["handheld"] = {
            "enabled": self.machine.state is GameState.HANDHELD,
            "game": self.machine.handheld_game,
            "generic": self.machine.handheld_generic,
        }
        try:
            atomic_write_json(state_path(), payload)
        except OSError as exc:
            self.log.debug("state save failed: %s", exc)

    # ---- tray ------------------------------------------------------------
    def _status(self) -> TrayStatus:
        error = self._obs_error or self.presence.error
        description = self.machine.describe()
        if self.paused:
            description = f"Paused — {description}"
        return TrayStatus(
            state=self.machine.state.value,
            description=description,
            game=self.machine.current_game,
            paused=self.paused,
            handheld=self.machine.state is GameState.HANDHELD,
            obs_connected=self._obs_connected,
            discord_connected=self.presence.connected,
            discord_enabled=self.presence.enabled,
            last_game=self.machine.last_game,
            games=tuple(entry.canonical for entry in self.games.recent(20)),
            error=error,
        )

    def _refresh_tray(self, now: float) -> None:
        if not self._tray:
            return
        status = self._status()
        self._tray.update(status)

    # ---- calibration -----------------------------------------------------
    def _run_calibration(self) -> None:
        if self.paused:
            self.log.info("Calibration while paused")
        self.log.info("Starting calibration wizard")
        result = calibration.run_wizard(
            self.obs,
            self.cfg,
            self.reader,
            self.log,
            should_stop=lambda: self.stop_event.is_set(),
        )
        if result is None:
            self.log.info("Calibration cancelled")
            if self._tray:
                self._tray.notify("Calibration cancelled")
            return
        save_config(self.cfg, self.config_file)
        self._power = self._build_power_detector()
        self._home = HomeDetector(
            threshold=float(get(self.cfg, "analysis.home_match_threshold", 0.80)),
            debounce_s=float(get(self.cfg, "analysis.home_debounce_s", 1.0)),
            labels_required=int(get(self.cfg, "ocr.dock_fallback_labels", 3)),
        )
        self._dock_template = self._load_dock_template()
        self._next_power_at = 0.0
        self._next_vision_at = 0.0
        if self._tray:
            self._tray.notify("Calibration saved")

    # ---- shutdown --------------------------------------------------------
    def shutdown(self) -> None:
        now = time.monotonic()
        try:
            self._sync_session(now)
            self._flush_session(now)
            self.games.save()
            self._save_state(now)
        except Exception as exc:
            self.log.debug("final save failed: %s", exc)
        self.presence.close()
        self.obs.close()
        self.log.info("Shut down")
