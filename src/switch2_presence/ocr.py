"""Title OCR orchestration: preprocessing, matching, and dock fallback."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from .games import SYSTEM_APPS, GameDB, Match, is_system_app, normalize_text
from .vision import ocr_ready_crop, preprocess_variants
from .win_ocr import OcrLine, OcrUnavailable, create_engine

_ALNUM = re.compile(r"[A-Za-z0-9]")
_EDGE_PUNCT = "@#$~^|\\/<>_+=*[]"
_KEEP_SINGLE = {"a", "i"}


def clean_text(text: str) -> str:
    value = normalize_text(text.replace("\r", " ").replace("\n", " "))
    value = re.sub(r"\s+", " ", value).strip()
    return value


def clean_title_text(text: str) -> str:
    """Clean OCR text and drop UI icon glyphs that leak into the title line.

    The HOME title row can render a small icon left of the title (read as a
    standalone ``O``/``e``/``B``) and a badge after it (read as ``@``).
    """
    value = clean_text(text)
    tokens = value.split(" ")
    if tokens:
        tokens[0] = tokens[0].strip(_EDGE_PUNCT)
        tokens[-1] = tokens[-1].strip(_EDGE_PUNCT)
    while tokens and not tokens[0]:
        tokens.pop(0)
    while tokens and not tokens[-1]:
        tokens.pop()
    if (
        len(tokens) >= 3
        and len(tokens[0]) == 1
        and tokens[0].isalpha()
        and tokens[0].casefold() not in _KEEP_SINGLE
    ):
        rest = sum(ch.isalnum() for ch in " ".join(tokens[1:]))
        if rest >= 6:
            tokens.pop(0)
    return " ".join(tokens)


@dataclass
class OcrReading:
    raw: str
    clean: str
    match: Match | None
    is_system: bool
    score: float

    @property
    def quality(self) -> int:
        return len(_ALNUM.findall(self.clean))

    @property
    def usable(self) -> bool:
        return self.quality >= 3 and not self.is_system


class TitleReader:
    def __init__(
        self,
        games: GameDB,
        language: str = "en-US",
        scale: int = 2,
        cutoff: int = 80,
        engine_kind: str = "windows",
        dock_labels_required: int = 3,
        base_width: int = 1920,
    ) -> None:
        self.games = games
        self.language = language
        self.scale = scale
        self.cutoff = cutoff
        self.base_width = base_width
        self.dock_labels_required = dock_labels_required
        self.engine = create_engine(engine_kind, language)
        self._disabled_reason: str | None = None

    @property
    def available(self) -> bool:
        if self._disabled_reason:
            return False
        return bool(getattr(self.engine, "available", True))

    def disable(self, reason: str) -> None:
        self._disabled_reason = reason

    def _recognize(self, image: Any) -> str:
        if self._disabled_reason:
            raise OcrUnavailable(self._disabled_reason)
        return self.engine.recognize(image)

    def _recognize_lines(self, image: Any) -> list[OcrLine]:
        if self._disabled_reason:
            raise OcrUnavailable(self._disabled_reason)
        recognize_lines = getattr(self.engine, "recognize_lines", None)
        if callable(recognize_lines):
            return list(recognize_lines(image))
        text = self.engine.recognize(image)
        return [OcrLine(text, 0.0, 0.0, 0.0, 0.0)]

    def read_title(self, frame: np.ndarray, region: Sequence[int]) -> OcrReading:
        """OCR the title band and choose the strongest line across all variants."""
        variants: list[np.ndarray] = [ocr_ready_crop(frame, region, self.scale, self.base_width)]
        variants.extend(preprocess_variants(frame, self.scale, self.base_width, region))

        candidates: list[OcrReading] = []
        for variant in variants:
            try:
                lines = self._recognize_lines(variant)
            except OcrUnavailable:
                raise
            except Exception:
                continue
            for line in lines:
                if len(_ALNUM.findall(line.text)) < 2:
                    continue
                candidates.append(self._evaluate(line.text))
        if not candidates:
            return OcrReading("", "", None, False, 0.0)

        candidates = self._prune_prefixes(candidates)
        matches = [reading for reading in candidates if reading.match is not None]
        if matches:
            return max(matches, key=lambda reading: (reading.score, reading.quality))
        return max(candidates, key=lambda reading: reading.quality)

    @staticmethod
    def _prune_prefixes(candidates: list[OcrReading]) -> list[OcrReading]:
        """Drop short fragments that are a prefix of a much longer reading."""
        kept: list[OcrReading] = []
        for reading in candidates:
            short = reading.clean.rstrip(".,;:").casefold()
            if not short:
                continue
            redundant = False
            for other in candidates:
                if other is reading:
                    continue
                long = other.clean.rstrip(".,;:").casefold()
                if (
                    len(long) >= len(short) + 4
                    and other.quality >= reading.quality
                    and long.startswith(short)
                ):
                    redundant = True
                    break
            if not redundant:
                kept.append(reading)
        return kept or candidates

    def _evaluate(self, text: str) -> OcrReading:
        clean = clean_title_text(text)
        system = is_system_app(clean)
        match = None if system else self.games.match(clean, self.cutoff)
        return OcrReading(
            raw=text,
            clean=clean,
            match=match,
            is_system=system,
            score=match.score if match else 0.0,
        )

    def dock_labels(self, frame: np.ndarray, region: Sequence[int]) -> tuple[int, str]:
        """OCR the dock strip and count recognized system labels."""
        crop = ocr_ready_crop(frame, region, self.scale, self.base_width)
        try:
            text = clean_text(self._recognize(crop))
        except OcrUnavailable:
            raise
        except Exception:
            return 0, ""
        lowered = text.casefold()
        found = 0
        for label in SYSTEM_APPS:
            token = label.casefold()
            if token in lowered or (len(token) >= 4 and token.split()[0] in lowered):
                found += 1
        return found, text

    def test_frame(self, frame: np.ndarray, title_region: Sequence[int]) -> OcrReading:
        return self.read_title(frame, title_region)
