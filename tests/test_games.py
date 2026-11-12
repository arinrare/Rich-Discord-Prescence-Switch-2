from __future__ import annotations

import json
from pathlib import Path

from switch2_presence.games import GameDB, is_system_app, normalize_text


def test_normalize_text() -> None:
    assert normalize_text("  Mario   Kart™  World… ") == "Mario Kart World"
    assert normalize_text("Kirby\u00ae") == "Kirby"
    assert normalize_text("FINAL FANTASY\u00a9") == "FINAL FANTASY"
    assert normalize_text("Pokémon™   Legends") == "Pokémon Legends"
    assert normalize_text("") == ""


def test_system_app_detection() -> None:
    assert is_system_app("eShop")
    assert is_system_app("Nintendo eShop")
    assert is_system_app("System Settings")
    assert is_system_app("GAME CHAT")
    assert not is_system_app("Mario Kart World")
    assert not is_system_app("")


def test_match_exact_alias_and_truncation(tmp_path: Path) -> None:
    db = GameDB(tmp_path / "games.json", cutoff=80)
    db.add("Mario Kart World", aliases=["MK World", "Mario Kart Wor…"], save=False)
    db.add("The Legend of Zelda: Tears of the Kingdom", save=False)

    assert db.match("Mario Kart World").canonical == "Mario Kart World"
    assert db.match("mk world").canonical == "Mario Kart World"
    assert db.match("Mario Kart Wor").canonical == "Mario Kart World"
    assert db.match("Mario Kart Wrold").canonical == "Mario Kart World"
    assert db.match("Zelda Tears of the Kingdom").canonical.startswith("The Legend of Zelda")
    assert db.match("Some Unknown Game") is None


def test_add_deduplicates_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "games.json"
    db = GameDB(path)
    entry, created = db.add("Metroid Prime 4", aliases=["Metroid Prime 4", "Metroid Prime 4…"])
    assert created
    # Aliases that normalize to the canonical title are dropped; truncated OCR
    # still matches through prefix scoring.
    assert entry.aliases == []
    db.add("metroid prime 4", aliases=["MP4"])
    assert db.match("MP4").canonical == "Metroid Prime 4"

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["games"][0]["canonical"] == "Metroid Prime 4"

    reloaded = GameDB(path)
    assert reloaded.all_names() == ["Metroid Prime 4"]
    assert reloaded.match("metroid prime 4").canonical == "Metroid Prime 4"


def test_record_seconds_and_recent(tmp_path: Path) -> None:
    db = GameDB(tmp_path / "games.json")
    db.add("Game A", save=False)
    db.add("Game B", save=False)
    db.record_seconds("Game A", 65, save=False)
    db.touch("Game A", save=False)
    db.touch("Game B", save=False)
    assert db.get("Game A").total_seconds == 65
    names = [entry.canonical for entry in db.recent(2)]
    assert set(names) == {"Game A", "Game B"}


def test_short_prefix_does_not_match(tmp_path: Path) -> None:
    db = GameDB(tmp_path / "games.json", cutoff=80)
    db.add("The Legend of Zelda: Tears of the Kingdom", save=False)
    assert db.match("The") is None
    assert db.match("Game") is None
