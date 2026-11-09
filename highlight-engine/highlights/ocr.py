"""Text recognition on small HUD crops, grouped into kill-feed lines."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Segment:
    text: str
    x: float       # left edge, in crop pixels
    y: float       # vertical center
    height: float
    conf: float


@dataclass
class Line:
    segments: list[Segment]

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments)


class Reader:
    """Wraps RapidOCR (runs on CPU through ONNX Runtime, no GPU needed)."""

    def __init__(self, min_conf: float = 0.5, upscale_below: int = 120):
        from rapidocr_onnxruntime import RapidOCR

        # By default RapidOCR blows every image up to 736px on its SHORT side, which
        # turns a small kill-feed crop into a ~2000px image and makes it 3-10x slower.
        # Read crops at their own size instead; HUD text doesn't need rotation checks.
        self._engine = RapidOCR(use_angle_cls=False, det_model_path=None,
                                det_limit_type="max", det_limit_side_len=1280)
        self.min_conf = min_conf
        self.upscale_below = upscale_below

    def segments(self, img: np.ndarray) -> list[Segment]:
        scale = 1.0
        # Only very small crops (e.g. a 720p VOD) need enlarging to be readable.
        if min(img.shape[:2]) < self.upscale_below:
            scale = 2.0
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        result, _ = self._engine(img)
        segs = []
        for pts, text, conf in result or []:
            conf = float(conf)
            text = text.strip()
            if not text or conf < self.min_conf:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            segs.append(Segment(text, min(xs) / scale, (min(ys) + max(ys)) / 2 / scale,
                                (max(ys) - min(ys)) / scale, conf))
        return segs

    def lines(self, img: np.ndarray) -> list[Line]:
        return group_lines(self.segments(img))


def group_lines(segs: list[Segment]) -> list[Line]:
    """Group OCR segments that sit on the same row, ordered left to right."""
    rows: list[list[Segment]] = []
    for s in sorted(segs, key=lambda s: s.y):
        if rows and abs(rows[-1][-1].y - s.y) < max(4.0, 0.6 * s.height):
            rows[-1].append(s)
        else:
            rows.append([s])
    return [Line(sorted(r, key=lambda s: s.x)) for r in rows]
