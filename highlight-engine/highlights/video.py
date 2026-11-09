"""Thin wrappers around ffprobe / ffmpeg."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np


@dataclass
class VideoInfo:
    width: int
    height: int
    duration: float


def probe(path: Path | str) -> VideoInfo:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    data = json.loads(out)
    stream = data["streams"][0]
    return VideoInfo(int(stream["width"]), int(stream["height"]), float(data["format"]["duration"]))


def iter_frames(
    path: Path | str,
    start: float,
    end: float,
    fps: float,
    crop: tuple[int, int, int, int] | None = None,
) -> Iterator[tuple[float, np.ndarray]]:
    """Yield (timestamp, BGR frame) pairs sampled at `fps` between start and end.

    `crop` is (x, y, w, h) in pixels. Cropping inside ffmpeg means only the
    kill-feed area is sent through the pipe, not the full frame.
    """
    info = probe(path)
    x, y, w, h = crop if crop else (0, 0, info.width, info.height)
    vf = f"fps={fps}"
    if crop:
        vf += f",crop={w}:{h}:{x}:{y}"
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
           "-i", str(path), "-an", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    frame_bytes = w * h * 3
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    assert proc.stdout is not None
    i = 0
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            yield start + i / fps, np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            i += 1
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()


def grab_frame(path: Path | str, t: float) -> np.ndarray:
    info = probe(path)
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(out, np.uint8).reshape(info.height, info.width, 3).copy()


def cut(src: Path | str, dst: Path | str, start: float, end: float, copy: bool = False) -> None:
    """Cut [start, end] from src. Re-encodes by default so cuts land on the exact frame."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{end - start:.3f}"]
    if copy:
        cmd += ["-c", "copy"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
    cmd.append(str(dst))
    subprocess.run(cmd, check=True)
