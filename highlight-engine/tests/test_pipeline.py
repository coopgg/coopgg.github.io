"""End-to-end test on a fake VOD with a drawn-on kill feed.

Run:  python -m pytest tests -q
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from highlights import config as cfg
from highlights.cli import main
from highlights.clips import build_clips
from highlights.detect import Event, classify_line
from highlights.ocr import Line, Segment

FONT = next((p for p in [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
] if Path(p).exists()), None)

# (start, end, y pixel, text) drawn inside the Destiny 2 kill-feed box at 1080p.
FEED = [
    (10, 16, 790, "CoopGG   >   Enemy One"),
    (12, 18, 750, "CoopGG   >   Enemy Two"),
    (14, 20, 710, "CoopGG   >   Enemy Three"),
    (25, 31, 790, "Teammate   >   Enemy Four"),   # not ours: ignored
    (35, 41, 790, "CoopGG   >   Enemy Five"),
    (48, 54, 790, "Enemy Six   >   CoopGG"),      # our death: not a clip
]


def make_fake_vod(path: Path) -> None:
    draws = ",".join(
        f"drawtext=fontfile={FONT}:text='{text}':x=60:y={y}:fontsize=30:fontcolor=white:"
        f"borderw=2:bordercolor=black:enable='between(t,{s},{e})'"
        for s, e, y, text in FEED
    )
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=c=0x2a3b4c:s=1920x1080:r=30:d=60",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-vf", f"noise=alls=25:allf=t,{draws}",
        "-t", "60", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path),
    ], check=True)


@pytest.mark.skipif(FONT is None or shutil.which("ffmpeg") is None, reason="needs ffmpeg and a font")
def test_end_to_end(tmp_path):
    vod = tmp_path / "fake.mp4"
    make_fake_vod(vod)
    out = tmp_path / "out"
    main(["analyze", str(vod), "--game", "destiny2", "--gamertag", "CoopGG", "--out", str(out)])

    events = json.loads((out / "events.json").read_text())
    kills = [e for e in events if e["kind"] == "kill"]
    deaths = [e for e in events if e["kind"] == "death"]
    assert len(kills) == 4, events
    assert len(deaths) == 1, events

    clips = sorted(json.loads((out / "clips.json").read_text())["clips"], key=lambda c: c["rank"])
    assert len(clips) == 2
    assert clips[0]["label"] == "triple" and 9 <= clips[0]["first_event"] <= 11
    assert clips[1]["label"] == "kill" and 34 <= clips[1]["first_event"] <= 36

    main(["cut", str(out), "--top", "1"])
    cut = list((out / "clips").glob("*.mp4"))
    assert len(cut) == 1 and cut[0].stat().st_size > 0


def _line(*parts):
    return Line([Segment(p, i * 200, 10, 20, 0.9) for i, p in enumerate(parts)])


def test_classify_killer_vs_victim():
    region = cfg.Region("killfeed", (0, 0, 1, 1))
    assert classify_line(_line("CoopGG", "Enemy"), region, "CoopGG")[0] == "kill"
    assert classify_line(_line("Enemy", "CoopGG"), region, "CoopGG")[0] == "death"
    assert classify_line(_line("C0opGG", "Enemy"), region, "CoopGG")[0] == "kill"  # OCR typo
    assert classify_line(_line("Teammate", "Enemy"), region, "CoopGG") is None


def test_keywords():
    region = cfg.Region("popup", (0, 0, 1, 1), mode="keywords", keywords={"eliminated": 1, "squad wipe": 3})
    assert classify_line(_line("SQUAD WIPE"), region, "x") == ("bonus", 3)
    assert classify_line(_line("nothing here"), region, "x") is None


def test_chaining_and_ranking():
    games = cfg.load()
    ev = [Event(t, "warzone", "killfeed", "kill", 1, "") for t in (100, 105, 112, 300)]
    clips = build_clips(ev, games, 1000)
    assert [c.kills for c in clips] == [3, 1]
    assert clips[0].rank == 1 and clips[0].start == 92 and clips[0].end == 115


def test_save_box_keeps_comments(tmp_path):
    p = tmp_path / "games.yaml"
    shutil.copy(cfg.DEFAULT_CONFIG, p)
    cfg.save_box(p, "warzone", "killfeed", (0.1, 0.2, 0.3, 0.4))
    games = cfg.load(p)
    assert games["warzone"].regions[0].box == (0.1, 0.2, 0.3, 0.4)
    assert games["destiny2"].regions[0].box == (0.02, 0.60, 0.33, 0.20)
    assert "# Per-game detection settings." in p.read_text()
