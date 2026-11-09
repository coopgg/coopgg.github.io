"""Work out which game is on screen when, from the VOD's Twitch chapters."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import Game


@dataclass
class Segment:
    game: Game
    start: float
    end: float
    title: str


def info_json_for(vod: Path) -> Path | None:
    """yt-dlp writes `<name>.info.json` next to the video."""
    for cand in (vod.with_suffix(".info.json"), vod.parent / (vod.stem + ".info.json")):
        if cand.exists():
            return cand
    return None


def match_game(title: str, games: dict[str, Game]) -> Game | None:
    t = title.lower()
    for g in games.values():
        if any(m in t for m in g.match):
            return g
    return None


def find_segments(
    vod: Path,
    duration: float,
    games: dict[str, Game],
    forced_game: str | None = None,
    start: float = 0.0,
    end: float | None = None,
) -> tuple[list[Segment], list[str]]:
    """Return (segments to scan, notes for the user)."""
    end = duration if end is None else min(end, duration)
    notes: list[str] = []
    if forced_game:
        if forced_game not in games:
            raise SystemExit(f"Unknown game '{forced_game}'. Known: {', '.join(games)}")
        return [Segment(games[forced_game], start, end, "(--game)")], notes

    info = info_json_for(vod)
    chapters = []
    if info:
        data = json.loads(info.read_text())
        chapters = data.get("chapters") or []
        if not chapters and data.get("game"):
            chapters = [{"start_time": 0, "end_time": duration, "title": data["game"]}]
    if not chapters:
        raise SystemExit(
            "No chapter info found for this VOD, so I can't tell which game is on screen.\n"
            "Either download it with `download` (which saves chapters), or pass --game "
            f"({', '.join(games)})."
        )

    segs = []
    for ch in chapters:
        s = max(float(ch.get("start_time", 0)), start)
        e = min(float(ch.get("end_time") or duration), end)
        title = ch.get("title", "")
        if e <= s:
            continue
        g = match_game(title, games)
        if g is None:
            notes.append(f"Skipping {fmt_time(s)}-{fmt_time(e)} '{title}' (no game config for it)")
            continue
        segs.append(Segment(g, s, e, title))
    return segs, notes


def fmt_time(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}"


def parse_time(s: str) -> float:
    """Accept 90, 1:30, or 1:01:30."""
    parts = [float(p) for p in s.split(":")]
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total
