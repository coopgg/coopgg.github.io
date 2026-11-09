"""Group kill events into clips and rank them."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .config import Game
from .detect import Event


@dataclass
class Clip:
    game: str
    start: float
    end: float
    kills: int
    score: float
    first_event: float
    last_event: float
    events: list[dict] = field(default_factory=list)
    rank: int = 0

    @property
    def label(self) -> str:
        names = {1: "kill", 2: "double", 3: "triple", 4: "quad"}
        return names.get(self.kills, f"{self.kills}x" if self.kills else "moment")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["label"] = self.label
        return d


def build_clips(events: list[Event], games: dict[str, Game], duration: float) -> list[Clip]:
    """Chain nearby kills into one clip, pad each clip, merge overlaps and rank.

    A multikill scores more than the same kills spread out: each extra kill in a
    chain adds 50% to the clip's score.
    """
    scoring = sorted((e for e in events if e.score > 0), key=lambda e: e.t)
    chains: list[list[Event]] = []
    for e in scoring:
        gap = games[e.game].chain_gap
        if chains and chains[-1][-1].game == e.game and e.t - chains[-1][-1].t <= gap:
            chains[-1].append(e)
        else:
            chains.append([e])

    clips: list[Clip] = []
    for chain in chains:
        g = games[chain[0].game]
        kills = sum(1 for e in chain if e.kind == "kill")
        base = sum(e.score for e in chain)
        clip = Clip(
            game=g.id,
            start=max(0.0, chain[0].t - g.pre_roll),
            end=min(duration, chain[-1].t + g.post_roll),
            kills=kills,
            score=round(base * (1 + 0.5 * max(0, kills - 1)), 2),
            first_event=chain[0].t,
            last_event=chain[-1].t,
            events=[e.to_dict() for e in chain],
        )
        if clips and clips[-1].game == clip.game and clip.start <= clips[-1].end:
            prev = clips[-1]
            prev.end = max(prev.end, clip.end)
            prev.kills += clip.kills
            prev.events += clip.events
            prev.last_event = clip.last_event
            base = sum(e["score"] for e in prev.events)
            prev.score = round(base * (1 + 0.5 * max(0, prev.kills - 1)), 2)
        else:
            clips.append(clip)

    for i, c in enumerate(sorted(clips, key=lambda c: (-c.score, c.start)), 1):
        c.rank = i
    return clips
