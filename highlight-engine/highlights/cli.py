"""Command line: download, analyze, cut, calibrate, snapshot, ocr-debug."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from . import config as cfg
from . import video
from .clips import Clip, build_clips
from .detect import scan
from .segments import find_segments, fmt_time, parse_time

ROOT = Path(__file__).resolve().parent.parent


def _games(args):
    games = cfg.load(args.config)
    if getattr(args, "gamertag", None):
        for g in games.values():
            g.gamertag = args.gamertag
    return games


def _out_dir(args, vod: Path) -> Path:
    out = Path(args.out) if args.out else ROOT / "output" / vod.stem
    out.mkdir(parents=True, exist_ok=True)
    return out


# ---------------------------------------------------------------- download
def cmd_download(args) -> Path:
    dest = Path(args.dir)
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "yt_dlp", args.url,
           "-f", f"bv*[height<={args.max_height}]+ba/b[height<={args.max_height}]/b",
           "--merge-output-format", "mp4", "--write-info-json",
           "-o", str(dest / "%(upload_date)s_%(id)s.%(ext)s"),
           "--print", "after_move:filepath", "--no-simulate"]
    path = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, text=True).stdout.strip().splitlines()[-1]
    print(f"Saved {path}")
    return Path(path)


# ---------------------------------------------------------------- analyze
def cmd_analyze(args) -> Path:
    from .ocr import Reader

    vod = Path(args.vod)
    games = _games(args)
    info = video.probe(vod)
    start = parse_time(args.start) if args.start else 0.0
    end = parse_time(args.end) if args.end else None
    segs, notes = find_segments(vod, info.duration, games, args.game, start, end)
    for n in notes:
        print(n)
    for s in segs:
        if not s.game.gamertag or s.game.gamertag == "YOUR_GAMERTAG":
            raise SystemExit(f"Set your gamertag for {s.game.name} in config/games.yaml or pass --gamertag.")

    reader = Reader()
    events = []
    t0 = time.time()
    for s in segs:
        print(f"Scanning {s.game.name} {fmt_time(s.start)}-{fmt_time(s.end)} as '{s.game.gamertag}'")
        last = [0.0]

        def progress(t, s=s, last=last):
            if time.time() - last[0] > 5:
                last[0] = time.time()
                pct = 100 * (t - s.start) / max(1, s.end - s.start)
                print(f"  {fmt_time(t)}  {pct:5.1f}%", flush=True)

        found = scan(str(vod), s.game, s.start, s.end, reader, progress=progress)
        events.extend(found)
    kills = sum(e.kind == "kill" for e in events)
    deaths = sum(e.kind == "death" for e in events)
    clips = build_clips(events, games, info.duration)

    out = _out_dir(args, vod)
    (out / "events.json").write_text(json.dumps([e.to_dict() for e in events], indent=2))
    (out / "clips.json").write_text(json.dumps(
        {"vod": str(vod.resolve()), "clips": [c.to_dict() for c in clips]}, indent=2))
    print(f"\nDone in {time.time() - t0:.0f}s: {kills} kills, {deaths} deaths, {len(clips)} clips")
    for c in sorted(clips, key=lambda c: c.rank)[:15]:
        print(f"  #{c.rank:<3} {fmt_time(c.start)}  {c.end - c.start:4.0f}s  {c.game:<9} "
              f"{c.label:<7} score {c.score}")
    print(f"Wrote {out / 'clips.json'}")
    return out


# ---------------------------------------------------------------- cut
def cmd_cut(args) -> None:
    out = Path(args.out_dir)
    data = json.loads((out / "clips.json").read_text())
    vod = Path(data["vod"])
    clips = sorted(data["clips"], key=lambda c: c["rank"])
    if args.top:
        clips = clips[: args.top]
    clip_dir = out / "clips"
    clip_dir.mkdir(exist_ok=True)
    for c in clips:
        name = f"{c['rank']:02d}_{c['game']}_{c['label']}_{fmt_time(c['start']).replace(':', '-')}.mp4"
        print(f"Cutting {name}")
        video.cut(vod, clip_dir / name, c["start"], c["end"], copy=args.fast)
    print(f"{len(clips)} clips in {clip_dir}")


def cmd_run(args) -> None:
    if args.vod.startswith(("http://", "https://")):
        args.vod = str(cmd_download(argparse.Namespace(url=args.vod, dir=args.dir, max_height=args.max_height)))
    out = cmd_analyze(args)
    cmd_cut(argparse.Namespace(out_dir=out, top=args.top, fast=args.fast))


# ---------------------------------------------------------------- tuning tools
def _draw_boxes(frame, game):
    import cv2

    h, w = frame.shape[:2]
    for r in game.regions:
        x, y, bw, bh = r.pixels(w, h)
        color = (0, 255, 0) if r.enabled else (0, 165, 255)
        cv2.rectangle(frame, (x, y), (x + bw, y + bh), color, 2)
        cv2.putText(frame, r.name + ("" if r.enabled else " (off)"), (x, max(20, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    return frame


def cmd_snapshot(args) -> None:
    import cv2

    games = _games(args)
    game = games[args.game]
    frame = _draw_boxes(video.grab_frame(args.vod, parse_time(args.at)), game)
    dst = Path(args.png or ROOT / "output" / f"snapshot_{args.game}_{args.at.replace(':', '-')}.png")
    dst.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dst), frame)
    print(f"Wrote {dst} (green = active box, orange = disabled)")


def cmd_calibrate(args) -> None:
    import cv2

    games = _games(args)
    game = games[args.game]
    frame = video.grab_frame(args.vod, parse_time(args.at))
    h, w = frame.shape[:2]
    names = [args.region] if args.region else [r.name for r in game.regions]
    # Show a scaled-down window so 1440p/4K frames fit on screen.
    scale = min(1.0, 1600 / w)
    shown = cv2.resize(frame, None, fx=scale, fy=scale) if scale < 1 else frame
    for name in names:
        title = f"{game.name}: drag a box around '{name}', ENTER to save, C to skip"
        x, y, bw, bh = cv2.selectROI(title, shown, showCrosshair=False)
        cv2.destroyWindow(title)
        if bw == 0 or bh == 0:
            print(f"Skipped {name}")
            continue
        box = (x / scale / w, y / scale / h, bw / scale / w, bh / scale / h)
        cfg.save_box(args.config, game.id, name, box)
        print(f"Saved {game.id}.{name} = [{', '.join(f'{v:.4f}' for v in box)}]")


def cmd_ocr_debug(args) -> None:
    from .detect import classify_line
    from .ocr import Reader

    games = _games(args)
    game = games[args.game]
    start = parse_time(args.start)
    end = parse_time(args.end) if args.end else start + 60
    for r in game.regions:
        r.enabled = True  # read disabled regions too, so you can find keywords for them
    regions = {r.name: r for r in game.regions}
    seen: dict[str, float] = {}

    def on_line(t, region, line):
        key = f"{region}:{line.text}"
        if t - seen.get(key, -99) < 3:
            return
        seen[key] = t
        verdict = classify_line(line, regions[region], game.gamertag) if game.gamertag else None
        tag = f"  -> {verdict[0]}" if verdict else ""
        print(f"{fmt_time(t)}  [{region}]  {line.text}{tag}")

    scan(args.vod, game, start, end, Reader(), on_line=on_line)


# ---------------------------------------------------------------- main
def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="highlights", description="Find kill-feed highlights in your VODs.")
    p.add_argument("--config", default=str(cfg.DEFAULT_CONFIG), help="path to games.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, out=True):
        sp.add_argument("--game", help="game id from games.yaml; skips chapter detection")
        sp.add_argument("--gamertag", help="override the gamertag in games.yaml")
        sp.add_argument("--start", help="only scan from this time (e.g. 1:05:00)")
        sp.add_argument("--end", help="only scan until this time")
        if out:
            sp.add_argument("--out", help="output folder (default: output/<vod name>)")

    sp = sub.add_parser("download", help="download a Twitch/YouTube VOD with its chapter info")
    sp.add_argument("url")
    sp.add_argument("--dir", default=str(ROOT / "vods"))
    sp.add_argument("--max-height", type=int, default=1080)
    sp.set_defaults(func=cmd_download)

    sp = sub.add_parser("analyze", help="find kills and write clips.json")
    sp.add_argument("vod")
    common(sp)
    sp.set_defaults(func=cmd_analyze)

    sp = sub.add_parser("cut", help="cut clips listed in an analyze output folder")
    sp.add_argument("out_dir")
    sp.add_argument("--top", type=int, help="only the N best clips")
    sp.add_argument("--fast", action="store_true", help="stream copy: much faster, cuts less exact")
    sp.set_defaults(func=cmd_cut)

    sp = sub.add_parser("run", help="download (if URL) + analyze + cut")
    sp.add_argument("vod", help="VOD file or URL")
    common(sp)
    sp.add_argument("--top", type=int, default=20)
    sp.add_argument("--fast", action="store_true")
    sp.add_argument("--dir", default=str(ROOT / "vods"))
    sp.add_argument("--max-height", type=int, default=1080)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("calibrate", help="drag boxes around the kill feed on a frame of your VOD")
    sp.add_argument("vod")
    sp.add_argument("--game", required=True)
    sp.add_argument("--at", required=True, help="timestamp showing the HUD, e.g. 1:23:45")
    sp.add_argument("--region", help="only this region (default: all)")
    sp.set_defaults(func=cmd_calibrate)

    sp = sub.add_parser("snapshot", help="save a frame with the boxes drawn on it")
    sp.add_argument("vod")
    sp.add_argument("--game", required=True)
    sp.add_argument("--at", required=True)
    sp.add_argument("--png")
    sp.set_defaults(func=cmd_snapshot)

    sp = sub.add_parser("ocr-debug", help="print what the OCR reads in each box")
    sp.add_argument("vod")
    sp.add_argument("--game", required=True)
    sp.add_argument("--gamertag")
    sp.add_argument("--start", required=True)
    sp.add_argument("--end")
    sp.set_defaults(func=cmd_ocr_debug)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
