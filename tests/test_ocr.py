from __future__ import annotations

from pathlib import Path

import numpy as np

from switch2_presence.games import GameDB
from switch2_presence.ocr import TitleReader, clean_title_text
from switch2_presence.win_ocr import OcrLine


class FakeEngine:
    available = True

    def __init__(self, lines: list[OcrLine]) -> None:
        self.lines = lines

    def recognize(self, image: object) -> str:
        return "\n".join(line.text for line in self.lines)

    def recognize_lines(self, image: object) -> list[OcrLine]:
        return list(self.lines)


def make_reader(tmp_path: Path, lines: list[OcrLine]) -> TitleReader:
    reader = TitleReader(GameDB(tmp_path / "games.json"), cutoff=80)
    reader.engine = FakeEngine(lines)  # type: ignore[assignment]
    return reader


def frame() -> np.ndarray:
    return np.zeros((80, 400, 3), dtype=np.uint8)


def line(text: str, x: float = 0.0, y: float = 0.0) -> OcrLine:
    return OcrLine(text, x, y, 200.0, 30.0)


def test_full_width_band_reads_title_wherever_it_is(tmp_path: Path) -> None:
    reader = make_reader(tmp_path, [line("Nintendo Switch Sports Resort", x=120)])
    reading = reader.read_title(frame(), [0, 0, 400, 80])
    assert reading.clean == "Nintendo Switch Sports Resort"


def test_short_fragment_does_not_beat_long_title(tmp_path: Path) -> None:
    db = GameDB(tmp_path / "games.json", cutoff=80)
    db.add("Nintendo.", save=False)
    reader = TitleReader(db, cutoff=80)
    reader.engine = FakeEngine(  # type: ignore[assignment]
        [line("Nintendo.", x=0), line("Nintendo Switch Sports Resort", x=120)]
    )
    reading = reader.read_title(frame(), [0, 0, 400, 80])
    assert reading.clean == "Nintendo Switch Sports Resort"
    assert reading.match is None


def test_matching_game_wins_over_system_line(tmp_path: Path) -> None:
    db = GameDB(tmp_path / "games.json", cutoff=80)
    db.add("Mario Kart World", save=False)
    reader = TitleReader(db, cutoff=80)
    reader.engine = FakeEngine(  # type: ignore[assignment]
        [line("System Settings", x=0), line("Mario Kart World", x=100)]
    )
    reading = reader.read_title(frame(), [0, 0, 400, 80])
    assert reading.match is not None
    assert reading.match.canonical == "Mario Kart World"


def test_longest_line_chosen_without_match(tmp_path: Path) -> None:
    reader = make_reader(
        tmp_path,
        [line("Mario", x=0), line("Mario Kart World", x=80)],
    )
    reading = reader.read_title(frame(), [0, 0, 400, 80])
    assert reading.clean == "Mario Kart World"


def test_empty_lines_return_empty_reading(tmp_path: Path) -> None:
    reader = make_reader(tmp_path, [line("e")])
    reading = reader.read_title(frame(), [0, 0, 400, 80])
    assert reading.clean == ""
    assert reading.match is None


def test_clean_title_strips_round_edge_glyph() -> None:
    assert clean_title_text("O Nintendo Switch Sports Resort") == "Nintendo Switch Sports Resort"
    assert clean_title_text("e Nintendo Switch Sports Resort") == "Nintendo Switch Sports Resort"
    assert clean_title_text("B Just Dance@") == "Just Dance"
    assert clean_title_text("[B] Just Dance") == "Just Dance"
    assert clean_title_text("A Plague Tale") == "A Plague Tale"
    assert clean_title_text("I Am Setsuna") == "I Am Setsuna"
    assert clean_title_text("Xenoblade Chronicles X") == "Xenoblade Chronicles X"
    assert clean_title_text("Final Fantasy X") == "Final Fantasy X"
    assert clean_title_text("Just Dance 2") == "Just Dance 2"
