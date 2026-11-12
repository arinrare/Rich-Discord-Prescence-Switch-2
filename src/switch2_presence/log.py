"""Rotating file logging with a stable logger name."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

LOGGER_NAME = "switch2_presence"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUPS = 5


def setup_logging(log_dir: Path | None = None, level: int = logging.INFO, console: bool = False) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    if any(getattr(h, "_switch2_handler", False) for h in logger.handlers):
        return logger
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "app.log", maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler._switch2_handler = True  # type: ignore[attr-defined]
        logger.addHandler(file_handler)

    if console or log_dir is None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        stream._switch2_handler = True  # type: ignore[attr-defined]
        logger.addHandler(stream)
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
