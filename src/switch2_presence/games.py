"""Local game database and fuzzy title matching."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, utils

from .config import atomic_write_json, games_path

SYSTEM_APPS: tuple[str, ...] = (
    "eShop",
    "Album",
    "System Settings",
    "Sleep Mode",
    "News",
    "GameChat",
    "Nintendo Switch Online",
    "Controllers",
    "GameShare",
    "Virtual Game Cards",
    "User Page",
    "Friends",
    "Parental Controls",
    "System Update",
    "Nintendo eShop",
)

SYSTEM_APP_ALIASES: tuple[str, ...] = (
    "Shop",
    "Albums",
    "Settings",
    "System Setting",
    "Sleep",
    "News Feed",
    "Game Chat",
    "Controller",
    "Switch Online",
    "Friend List",
    "Game Share",
    "Virtual Game Card",
)

_TRADEMARKS = ("\u2122", "\u00ae", "\u00a9", "\u2120")
_ELLIPSES = ("\u2026", "...", "..")
_WS = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Unicode/whitespace/trademark normalization used for OCR and matching."""
    if not text:
        return ""
    value = str(text)
    # NFKC turns ™ into the letters "TM", so strip symbols first.
    for mark in _TRADEMARKS:
        value = value.replace(mark, "")
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\u2026", "...")
    value = _WS.sub(" ", value).strip()
    for suffix in _ELLIPSES:
        while value.endswith(suffix):
            value = value[: -len(suffix)].rstrip()
    return value


def _casefold(text: str) -> str:
    return normalize_text(text).casefold()


def is_system_app(text: str) -> bool:
    query = _casefold(text)
    if not query:
        return False
    for name in (*SYSTEM_APPS, *SYSTEM_APP_ALIASES):
        candidate = name.casefold()
        if query == candidate:
            return True
        if len(query) >= 4 and len(candidate) >= 4:
            if fuzz.ratio(query, candidate) >= 88:
                return True
    return False


@dataclass
class GameEntry:
    canonical: str
    aliases: list[str] = field(default_factory=list)
    phash: str | None = None
    last_seen: str | None = None
    total_seconds: int = 0

    def all_names(self) -> list[str]:
        names = [self.canonical]
        for alias in self.aliases:
            if alias and alias not in names:
                names.append(alias)
        return names

    def to_json(self) -> dict[str, Any]:
        return {
            "canonical": self.canonical,
            "aliases": self.aliases,
            "phash": self.phash,
            "last_seen": self.last_seen,
            "total_seconds": int(self.total_seconds),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> GameEntry:
        canonical = normalize_text(str(data.get("canonical") or ""))
        aliases_raw = data.get("aliases") or []
        aliases = [normalize_text(str(a)) for a in aliases_raw if str(a).strip()]
        phash = data.get("phash")
        return cls(
            canonical=canonical,
            aliases=[a for a in aliases if a and a != canonical],
            phash=str(phash) if phash else None,
            last_seen=str(data["last_seen"]) if data.get("last_seen") else None,
            total_seconds=int(data.get("total_seconds") or 0),
        )


@dataclass
class Match:
    canonical: str
    score: float
    matched_text: str


class GameDB:
    def __init__(self, path: Path | str | None = None, cutoff: int = 80) -> None:
        self.path = Path(path) if path else games_path()
        self.cutoff = cutoff
        self.entries: dict[str, GameEntry] = {}
        self.load()

    # ---- persistence -----------------------------------------------------
    def load(self) -> None:
        self.entries = {}
        if not self.path.exists():
            return
        try:
            import json

            with self.path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return
        rows: Iterable[Any] = (
            raw.get("games", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
        )
        for row in rows:
            if not isinstance(row, dict):
                continue
            entry = GameEntry.from_json(row)
            if entry.canonical:
                self.entries[entry.canonical.casefold()] = entry

    def save(self) -> None:
        payload = {"games": [entry.to_json() for entry in self.entries.values()]}
        atomic_write_json(self.path, payload)

    # ---- query -----------------------------------------------------------
    def get(self, canonical: str) -> GameEntry | None:
        return self.entries.get(canonical.casefold())

    def all_names(self) -> list[str]:
        return [entry.canonical for entry in self.entries.values()]

    def recent(self, limit: int = 20) -> list[GameEntry]:
        def key(entry: GameEntry) -> str:
            return entry.last_seen or ""

        return sorted(self.entries.values(), key=key, reverse=True)[:limit]

    def match(self, text: str, cutoff: int | None = None) -> Match | None:
        query = normalize_text(text)
        if not query:
            return None
        threshold = self.cutoff if cutoff is None else cutoff
        q = query.casefold()
        best: Match | None = None
        for entry in self.entries.values():
            for candidate in entry.all_names():
                cand = normalize_text(candidate)
                if not cand:
                    continue
                c = cand.casefold()
                if q == c:
                    return Match(entry.canonical, 100.0, cand)
                score = float(fuzz.WRatio(query, cand, processor=utils.default_process))
                shorter = min(len(q), len(c))
                longer = max(len(q), len(c))
                prefix_boost = False
                if (
                    shorter >= 5
                    and shorter >= longer * 0.4
                    and (c.startswith(q) or q.startswith(c))
                ):
                    score = max(score, 95.0)
                    prefix_boost = True
                length_ratio = shorter / longer if longer else 0.0
                # WRatio's partial ratio gives long titles a high score against a
                # short DB fragment ("Nintendo."). Require comparable lengths
                # unless this is a deliberate prefix/truncation match.
                if (
                    score >= threshold
                    and (prefix_boost or length_ratio >= 0.45)
                    and (best is None or score > best.score)
                ):
                    best = Match(entry.canonical, score, cand)
        return best

    # ---- mutation --------------------------------------------------------
    def add(
        self,
        canonical: str,
        aliases: Iterable[str] | None = None,
        phash: str | None = None,
        save: bool = True,
    ) -> tuple[GameEntry, bool]:
        name = normalize_text(canonical)
        if not name:
            raise ValueError("canonical title must not be empty")
        key = name.casefold()
        created = False
        entry = self.entries.get(key)
        if entry is None:
            entry = GameEntry(canonical=name, aliases=[], phash=phash)
            self.entries[key] = entry
            created = True
        if phash:
            entry.phash = phash
        for alias in aliases or []:
            normalized = normalize_text(alias)
            if normalized and normalized.casefold() != key and normalized not in entry.aliases:
                entry.aliases.append(normalized)
        if save:
            self.save()
        return entry, created

    def touch(self, canonical: str, now: datetime | None = None, save: bool = True) -> GameEntry | None:
        entry = self.get(canonical)
        if entry is None:
            return None
        stamp = now or datetime.now(UTC)
        entry.last_seen = stamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
        if save:
            self.save()
        return entry

    def record_seconds(
        self, canonical: str | None, seconds: float, now: datetime | None = None, save: bool = True
    ) -> None:
        if not canonical or seconds <= 0:
            return
        entry = self.get(canonical)
        if entry is None:
            entry, _ = self.add(canonical, save=False)
        entry.total_seconds += int(round(seconds))
        self.touch(canonical, now=now, save=False)
        if save:
            self.save()
