"""Windows.Media.Ocr wrapper (winsdk) with an optional Tesseract fallback."""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from .vision import resize_max_dimension


class OcrUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class OcrLine:
    text: str
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height


def _to_pil(image: Any) -> Image.Image:
    if isinstance(image, Image.Image):
        return image.convert("RGB")
    array = np.asarray(image)
    if array.ndim == 2:
        return Image.fromarray(array).convert("RGB")
    if array.shape[2] == 4:
        return Image.fromarray(array[:, :, :3]).convert("RGB")
    return Image.fromarray(array).convert("RGB")


class WindowsOcr:
    def __init__(self, language: str = "en-US") -> None:
        self.language = language
        self._engine: Any = None
        self._max_dimension = 0
        self._error: str | None = None

    def _ensure_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        if self._error:
            raise OcrUnavailable(self._error)
        try:
            from winsdk.windows.globalization import Language
            from winsdk.windows.media.ocr import OcrEngine
        except Exception as exc:  # pragma: no cover - platform specific
            self._error = f"winsdk unavailable: {exc}"
            raise OcrUnavailable(self._error) from exc
        engine = None
        try:
            engine = OcrEngine.try_create_from_language(Language(self.language))
        except Exception:
            engine = None
        if engine is None:
            try:
                engine = OcrEngine.try_create_from_user_profile_languages()
            except Exception:
                engine = None
        if engine is None:
            self._error = f"no Windows OCR language pack available for {self.language!r}"
            raise OcrUnavailable(self._error)
        try:
            self._max_dimension = int(OcrEngine.max_image_dimension)
        except Exception:
            self._max_dimension = 0
        self._engine = engine
        return engine

    @property
    def available(self) -> bool:
        try:
            self._ensure_engine()
            return True
        except OcrUnavailable:
            return False

    def recognize(self, image: Any) -> str:
        return "\n".join(line.text for line in self.recognize_lines(image))

    def recognize_lines(self, image: Any) -> list[OcrLine]:
        self._ensure_engine()
        pil = _to_pil(image)
        if self._max_dimension:
            array = resize_max_dimension(np.asarray(pil), self._max_dimension)
            pil = Image.fromarray(array)
        buffer = io.BytesIO()
        pil.save(buffer, format="PNG")
        try:
            return asyncio.run(self._recognize_lines_async(buffer.getvalue()))
        except OcrUnavailable:
            raise
        except Exception as exc:  # pragma: no cover - platform specific
            raise OcrUnavailable(f"Windows OCR failed: {exc}") from exc

    async def _recognize_lines_async(self, png_bytes: bytes) -> list[OcrLine]:
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.storage.streams import (
            DataWriter,
            InMemoryRandomAccessStream,
        )

        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(png_bytes)
        await writer.store_async()
        await writer.flush_async()
        writer.detach_stream()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await self._engine.recognize_async(bitmap)
        lines: list[OcrLine] = []
        for line in result.lines:
            words = list(line.words)
            if not words:
                lines.append(OcrLine(str(line.text or ""), 0.0, 0.0, 0.0, 0.0))
                continue
            x0 = min(float(word.bounding_rect.x) for word in words)
            y0 = min(float(word.bounding_rect.y) for word in words)
            x1 = max(float(word.bounding_rect.x + word.bounding_rect.width) for word in words)
            y1 = max(float(word.bounding_rect.y + word.bounding_rect.height) for word in words)
            lines.append(OcrLine(str(line.text or ""), x0, y0, x1 - x0, y1 - y0))
        return sorted(lines, key=lambda item: (item.y, item.x))


class TesseractOcr:
    """Optional fallback when Windows OCR is unavailable."""

    def __init__(self, language: str = "eng") -> None:
        self.language = language

    @property
    def available(self) -> bool:
        try:
            import pytesseract

            pytesseract.get_tesseract_version()
            return True
        except Exception:
            return False

    def recognize(self, image: Any) -> str:
        try:
            import pytesseract
        except Exception as exc:  # pragma: no cover - optional dependency
            raise OcrUnavailable(f"pytesseract unavailable: {exc}") from exc
        pil = _to_pil(image)
        try:
            return str(pytesseract.image_to_string(pil, lang=self.language) or "")
        except Exception as exc:  # pragma: no cover - optional dependency
            raise OcrUnavailable(f"Tesseract failed: {exc}") from exc

    def recognize_lines(self, image: Any) -> list[OcrLine]:
        text = self.recognize(image)
        return [OcrLine(line, 0.0, 0.0, 0.0, 0.0) for line in text.splitlines() if line.strip()]


def create_engine(kind: str = "windows", language: str = "en-US") -> WindowsOcr | TesseractOcr:
    if kind.lower() == "tesseract":
        return TesseractOcr(language="eng" if language.startswith("en") else language)
    return WindowsOcr(language=language)
