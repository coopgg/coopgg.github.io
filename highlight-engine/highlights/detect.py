"""Turn kill-feed and pop-up text into timestamped events."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, Iterable

import numpy as np
from rapidfuzz import fuzz

from .config import Game, Region
from .ocr import Line, Reader
from . import video

MATCH_THRESHOLD = 82     # fuzzy score (0-100) for a gamertag or keyword match
SAME_LINE_THRESHOLD = 85  # two OCR reads this similar are the same feed line
PERSIST_SECONDS = 8.0     # how long a feed line can stay on screen
CHANGE_GATE = 3.0         # skip OCR when the region barely changed (mean pixel diff)


@dataclass
class Event:
    t: float
    game: str
    region: str
    kind: str      # "kill", "death" or "bonus"
    score: float
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


def classify_line(line: Line, region: Region, gamertag: str) -> tuple[str, float] | None:
    """Decide what one OCR'd line means for this region, or None if it's not about you."""
    text = line.text.lower()
    if region.mode == "keywords":
        best = None
        for kw, pts in region.keywords.items():
            if fuzz.partial_ratio(kw, text) >= MATCH_THRESHOLD:
                if best is None or pts > best[1]:
                    best = ("bonus", pts)
        return best

    tag = gamertag.lower()
    if not tag or len(text) < max(3, int(len(tag) * 0.6)):
        return None
    align = fuzz.partial_ratio_alignment(tag, text)
    if align is None or align.score < MATCH_THRESHOLD:
        return None
    # Killer is on the left in both Destiny 2 and Warzone feeds.
    if len(line.segments) > 1:
        killer_end = len(line.segments[0].text)
        is_killer = align.dest_start < killer_end
    else:
        is_killer = align.dest_start < len(text) / 2
    return ("kill", region.score) if is_killer else ("death", 0.0)


def dedupe_key(text: str, gamertag: str) -> str:
    """The part of a feed line that identifies it: the OTHER player's name.

    Drops single characters (OCR reads weapon icons as stray "I", ">", "E") and
    your own gamertag, so "CoopGG > E Enemy Two" and "CoopGG I Enemy Two" match,
    but "Enemy One" and "Enemy Two" stay different kills.
    """
    import re

    tag = gamertag.lower()
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 2]
    if tag:
        words = [w for w in words if fuzz.ratio(w, tag) < MATCH_THRESHOLD]
    return " ".join(words)


class _Dedupe:
    """Feed lines stay on screen for seconds; only report each one once."""

    def __init__(self):
        self.recent: list[tuple[str, float]] = []

    def is_new(self, text: str, t: float) -> bool:
        self.recent = [(s, seen) for s, seen in self.recent if t - seen <= PERSIST_SECONDS]
        for i, (s, _) in enumerate(self.recent):
            if fuzz.ratio(s, text) >= SAME_LINE_THRESHOLD:
                self.recent[i] = (s, t)  # still on screen; refresh
                return False
        self.recent.append((text, t))
        return True


def _union(boxes: Iterable[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    boxes = list(boxes)
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return x0, y0, x1 - x0, y1 - y0


def scan(
    vod: str,
    game: Game,
    start: float,
    end: float,
    reader: Reader,
    on_line: Callable[[float, str, Line], None] | None = None,
    progress: Callable[[float], None] | None = None,
) -> list[Event]:
    """Scan one stretch of the VOD for one game and return events in time order."""
    regions = game.active_regions
    if not regions:
        return []
    info = video.probe(vod)
    px = {r.name: r.pixels(info.width, info.height) for r in regions}
    ux, uy, uw, uh = _union(px.values())
    dedupe: dict[tuple[str, str], _Dedupe] = {}
    last_gray: dict[str, np.ndarray] = {}
    events: list[Event] = []

    for t, frame in video.iter_frames(vod, start, end, game.sample_fps, crop=(ux, uy, uw, uh)):
        if progress:
            progress(t)
        for r in regions:
            x, y, w, h = px[r.name]
            crop = frame[y - uy:y - uy + h, x - ux:x - ux + w]
            gray = crop.mean(axis=2)
            prev = last_gray.get(r.name)
            if prev is not None and np.abs(gray - prev).mean() < CHANGE_GATE:
                continue
            last_gray[r.name] = gray
            for line in reader.lines(crop):
                if on_line:
                    on_line(t, r.name, line)
                result = classify_line(line, r, game.gamertag)
                if result is None:
                    continue
                kind, score = result
                seen = dedupe.setdefault((r.name, kind), _Dedupe())
                if not seen.is_new(dedupe_key(line.text, game.gamertag), t):
                    continue
                events.append(Event(round(t, 2), game.id, r.name, kind, score, line.text))
    return events
