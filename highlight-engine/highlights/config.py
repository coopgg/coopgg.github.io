"""Load and save per-game detection settings (config/games.yaml)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "games.yaml"


@dataclass
class Region:
    name: str
    box: tuple[float, float, float, float]  # x, y, w, h as fractions of the frame
    mode: str = "gamertag"                   # "gamertag" or "keywords"
    score: float = 1.0
    keywords: dict[str, float] = field(default_factory=dict)
    enabled: bool = True

    def pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        x, y, w, h = self.box
        px, py = int(round(x * width)), int(round(y * height))
        pw = max(1, min(width - px, int(round(w * width))))
        ph = max(1, min(height - py, int(round(h * height))))
        return px, py, pw, ph


@dataclass
class Game:
    id: str
    name: str
    match: list[str]
    gamertag: str
    regions: list[Region]
    sample_fps: float = 2.0
    chain_gap: float = 6.0
    pre_roll: float = 8.0
    post_roll: float = 3.0

    @property
    def active_regions(self) -> list[Region]:
        return [r for r in self.regions if r.enabled]


def load(path: Path | str = DEFAULT_CONFIG) -> dict[str, Game]:
    data = yaml.safe_load(Path(path).read_text()) or {}
    default_tag = str(data.get("gamertag", "")).strip()
    games: dict[str, Game] = {}
    for gid, g in (data.get("games") or {}).items():
        regions = []
        for rname, r in (g.get("regions") or {}).items():
            box = tuple(float(v) for v in r["box"])
            if len(box) != 4:
                raise ValueError(f"{gid}.{rname}: box needs 4 numbers [x, y, w, h]")
            regions.append(Region(
                name=rname,
                box=box,  # type: ignore[arg-type]
                mode=r.get("mode", "gamertag"),
                score=float(r.get("score", 1)),
                keywords={str(k).lower(): float(v) for k, v in (r.get("keywords") or {}).items()},
                enabled=bool(r.get("enabled", True)),
            ))
        games[gid] = Game(
            id=gid,
            name=g.get("name", gid),
            match=[m.lower() for m in g.get("match", [gid])],
            gamertag=str(g.get("gamertag", default_tag)).strip(),
            regions=regions,
            sample_fps=float(g.get("sample_fps", 2)),
            chain_gap=float(g.get("chain_gap", 6)),
            pre_roll=float(g.get("pre_roll", 8)),
            post_roll=float(g.get("post_roll", 3)),
        )
    return games


def save_box(path: Path | str, game_id: str, region: str, box: tuple[float, ...]) -> None:
    """Update one region's box in the YAML file.

    Edits the matching `box:` line in place so the comments in the file are kept.
    """
    path = Path(path)
    lines = path.read_text().splitlines(keepends=True)
    new_box = "[" + ", ".join(f"{v:.4f}" for v in box) + "]"
    in_game = in_region = False
    game_indent = region_indent = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if stripped == f"{game_id}:":
            in_game, game_indent, in_region = True, indent, False
            continue
        if in_game and indent <= game_indent:
            in_game = False
        if in_game and stripped == f"{region}:":
            in_region, region_indent = True, indent
            continue
        if in_region and indent <= region_indent:
            in_region = False
        if in_region and stripped.startswith("box:"):
            comment = ""
            if "#" in stripped:
                comment = "  #" + stripped.split("#", 1)[1]
            lines[i] = " " * indent + f"box: {new_box}{comment}\n"
            path.write_text("".join(lines))
            return
    raise KeyError(f"No box found for {game_id}.{region} in {path}")
