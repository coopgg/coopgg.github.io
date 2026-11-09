# Highlight Engine

Finds your kills in a stream VOD by reading the game's **kill feed**, then cuts
them into ranked clips. It doesn't use your face cam, your mic or chat; it only
reads what the game puts on screen.

Supported to start: **Destiny 2** (PvP modes) and **Call of Duty Warzone / Resurgence**.
New games are a block in `config/games.yaml`.

Status: phase 1 (detect + cut). The montage / highlight reel and the BRB loop
are the next phase.

## How it works

1. **Which game is on screen:** read from the VOD's Twitch chapters, which change
   whenever you switch category. Chapters for games without a config
   (e.g. Just Chatting) are skipped.
2. **Kill feed:** twice a second it reads the kill feed box. A line where your
   gamertag is on the left (the killer side) is your kill; your name on the right
   is your death. Teammates' and enemies' kills are ignored. Small text-recognition
   mistakes are tolerated.
3. **Clips:** kills close together (6s in Destiny, 10s in Warzone) become one
   clip, padded with 8s of lead-in and 3s after. Multikills rank highest.

## Setup (Windows, Mac or Linux)

You need Python 3.10+ and ffmpeg on your PATH
(Windows: `winget install ffmpeg`).

```
cd highlight-engine
python -m venv .venv
.venv\Scripts\activate          # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Then open `config/games.yaml` and set `gamertag:` to your in-game name. If your
Activision name is different, set it under `warzone:` too.

## First-time calibration (once per game)

The default kill-feed boxes are rough guesses. Your resolution and HUD settings
move things around, so fix them on one of your own VODs:

```
python -m highlights download https://www.twitch.tv/videos/123456789
python -m highlights calibrate vods/<file>.mp4 --game warzone --at 1:02:30
```

Pick a timestamp where the kill feed has a few lines showing. A window opens:
drag a box around the kill feed, then press ENTER. Then check it:

```
python -m highlights snapshot vods/<file>.mp4 --game warzone --at 1:02:30
python -m highlights ocr-debug vods/<file>.mp4 --game warzone --start 1:02:00 --end 1:04:00
```

`ocr-debug` prints every line the tool reads in each box, and marks the ones it
counts as your kill or death. If your name keeps getting misread, that's where
you'll see it.

### Optional pop-up regions

Each game also has a disabled pop-up region: Warzone's elimination pop-up and
Destiny's Crucible medals. To use one, calibrate its box, run `ocr-debug` over a
few kills to see the exact words the game shows, add them as `keywords` with
points, and set `enabled: true`. For example:

```yaml
keywords: {"eliminated": 1, "squad wiped": 3}
```

## Everyday use

```
python -m highlights run https://www.twitch.tv/videos/123456789 --top 20
```

That downloads the VOD, finds the kills and cuts the 20 best clips into
`output/<vod>/clips/`, named by rank, e.g. `01_warzone_triple_1-23-45.mp4`.
`output/<vod>/clips.json` lists every clip with its score and the feed lines
that triggered it.

Other options:

| Command | What it does |
|---|---|
| `analyze VOD` | Detect only; writes `events.json` and `clips.json` |
| `cut output/<vod> --top 10` | Cut clips from an earlier analyze |
| `--game destiny2` | Skip chapter detection, e.g. for a local recording |
| `--start 1:00:00 --end 2:00:00` | Scan only part of the VOD |
| `--fast` | Cut without re-encoding: much faster, but the start can be off by a few seconds |

Speed: in testing it scanned about 3x faster than real time on a 4-core CPU, so a
3-hour VOD should take roughly an hour or less. It needs no GPU.

## Known limits

- **Destiny 2 PvE** (strikes, raids, dungeons) has no kill feed for normal
  enemies, so PvE needs a different signal, such as boss health bars or
  "encounter complete" text. That isn't built yet.
- Kills where the feed only shows an icon and no name can't be read.
- A really busy background behind a semi-transparent feed can cause misreads.
  `ocr-debug` will show it.

## Tests

```
pip install pytest
python -m pytest tests -q
```

The end-to-end test generates a fake 1080p VOD with a drawn-on kill feed
(a triple kill, a teammate's kill, a single kill and a death), checks that only
your 4 kills are found, and checks they're grouped into a triple and a single.
