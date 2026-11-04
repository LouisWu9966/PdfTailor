"""Estimating size, weight and slant of lines of scanned text.

Three things make absolute thresholds unreliable on scans, all measured on Noto Sans CJK
TC (Light..Black) and DejaVu rendered through blur, noise and JPEG:

* blur and thresholding thicken every stroke by a roughly constant ~1.3 px, so the same
  weight reads 0.093 em at 30 px and 0.071 em at 60 px;
* a bold serif face (0.050 em after correction) is as thick as a regular sans (0.050);
* a skewed scan leans *every* line, which is not italic.

So weight and slant are judged against the rest of the page, and only fall back to
absolute values when the page offers too little to compare with.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .imaging import distance_map
from .line import LineScan

# Constant extra stroke width, in pixels of the *original* render, added by blur and the
# ink threshold (fit of width-in-px against em-in-px; intercept 1.3 for Regular).
STROKE_EDGE_PX = 0.5  # contour length runs half a pixel outside the stroke
BOLD_RATIO = 1.24  # regular lines peak at 1.17, bold start at 1.31 (synthetic set, half-contrast strokes)
ABS_BOLD_CJK = 0.082  # corrected stroke width in ems, used only without page context
ABS_BOLD_LATIN = 0.095
ITALIC_SHEAR_MIN = 0.12  # real italics lean ~0.2 (11-12 degrees)
ITALIC_GAIN_MIN = 1.04


@dataclass(frozen=True)
class LineMetrics:
    stroke: float  # blur-corrected stroke width, in ems
    slant: float  # tan of the slant angle
    gain: float  # how much sharper the profile gets when un-slanted
    is_cjk: bool
    stroke_ok: bool  # enough ink to measure stroke width
    slant_ok: bool  # long enough to judge a slant


@dataclass(frozen=True)
class StyleEstimate:
    size_px: float  # in pixels of the original render
    stroke: float
    slant: float
    bold: bool
    bold_confidence: float
    italic: bool
    italic_confidence: float


def _text_mask(scan: LineScan) -> np.ndarray:
    comps = scan.text_comps
    mask = np.zeros(scan.mask.shape, dtype=np.uint8)
    for c in comps:
        sub = scan.labels[c.y : c.bottom, c.x : c.right] == c.label
        mask[c.y : c.bottom, c.x : c.right] |= sub.astype(np.uint8)
    return mask


def raw_stroke_em(scan: LineScan) -> float:
    """Mean stroke width over the line's ink, in ems.

    For a stroke of width ``w`` and length ``L``, area ≈ w·L and perimeter ≈ 2·L, so
    ``w ≈ 2·area / perimeter``: cheap, and it needs no skeleton.

    The ink is re-thresholded at *half contrast* between paper and ink. A blurred edge
    crosses half contrast exactly where the sharp edge was, so the width does not grow
    with scanner blur or with upsampling (a 200 dpi scan rendered at 400 dpi) — a fixed
    blur correction in pixels cannot do that, and it made small body text look heavier
    than a large title.
    """
    if scan.em <= 0 or len(scan.text_comps) == 0:
        return 0.0
    text = _text_mask(scan).astype(bool)
    if not text.any():
        return 0.0
    dist = distance_map(scan.rgb, scan.bg)
    ink_level = float(np.percentile(dist[text], 90))
    if ink_level <= 0:
        return 0.0
    region = cv2.dilate(text.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=2).astype(bool)
    mask = ((dist >= 0.5 * ink_level) & region).astype(np.uint8)
    area = float(mask.sum())
    cnts, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    peri = sum(cv2.arcLength(c, True) for c in cnts)
    if peri <= 0:
        return 0.0
    width_px = 2.0 * area / peri
    return (width_px - STROKE_EDGE_PX) / scan.em


def slant_of(scan: LineScan) -> tuple[float, float]:
    """(shear = tan of slant, gain) by projection-profile sharpening.

    Un-shearing an italic line stands its vertical strokes upright, which makes the
    column-sum profile spiky; the shear that maximises that spikiness is the slant.
    """
    comps = scan.text_comps
    if not comps:
        return 0.0, 1.0
    x0, x1 = min(c.x for c in comps), max(c.right for c in comps)
    y0, y1 = min(c.y for c in comps), max(c.bottom for c in comps)
    sub = scan.mask[y0:y1, x0:x1].astype(np.float32)
    h, w = sub.shape
    if h < 8 or w < 8:
        return 0.0, 1.0
    base = scan.baseline - y0

    def sharpness(shear: float) -> float:
        pad = int(abs(shear) * h) + 2
        padded = cv2.copyMakeBorder(sub, 0, 0, pad, pad, cv2.BORDER_CONSTANT, value=0)
        # forward map x' = x - shear*(base - y): upper rows shift left, standing a
        # right-leaning stroke upright; shearing about the baseline keeps the feet put
        m = np.float32([[1, shear, -shear * (base)], [0, 1, 0]])
        warped = cv2.warpAffine(padded, m, (w + 2 * pad, h), flags=cv2.INTER_LINEAR, borderValue=0)
        cols = warped.sum(axis=0)
        return float((cols**2).sum())

    shears = np.linspace(-0.12, 0.45, 20)
    scores = np.array([sharpness(float(s)) for s in shears])
    best = int(scores.argmax())
    upright = sharpness(0.0)
    gain = float(scores[best] / upright) if upright > 0 else 1.0
    return float(shears[best]), gain


def measure_line(scan: LineScan) -> LineMetrics:
    comps = scan.text_comps
    if scan.em <= 0 or not comps:
        return LineMetrics(0.0, 0.0, 1.0, scan.script == "cjk", False, False)
    # Weight needs ink, not blob count: a two-character header like 單價 is only two blobs
    # but plenty of ink. A slant needs length: two glyphs cannot define a lean.
    ink = sum(c.area for c in comps)
    stroke_ok = ink >= 0.5 * scan.em**2
    width = max(c.right for c in comps) - min(c.x for c in comps)
    slant_ok = len(comps) >= 4 and width >= 2.0 * scan.em
    shear, gain = slant_of(scan) if slant_ok else (0.0, 1.0)
    return LineMetrics(
        stroke=raw_stroke_em(scan) if stroke_ok else 0.0,
        slant=shear,
        gain=gain,
        is_cjk=scan.script == "cjk",
        stroke_ok=stroke_ok,
        slant_ok=slant_ok,
    )


def _median(vals: list[float]) -> float | None:
    return float(np.median(vals)) if vals else None


def _weighted_median(pairs: list[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    pairs = sorted(pairs)
    half = sum(w for _, w in pairs) / 2.0
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= half:
            return v
    return pairs[-1][0]


def estimate_styles(scans: list[LineScan]) -> list[StyleEstimate]:
    """Judges every line of a page against the others on that page."""
    metrics = [measure_line(s) for s in scans]
    weights = [max(1, len(s.text_comps)) for s in scans]

    ref_stroke: dict[bool, float | None] = {}
    for cjk in (True, False):
        # Weighted by glyph count: body text is most of the ink on a page, while a bold
        # title or a row of bold table headers is a few glyphs. An unweighted median over
        # *lines* is dragged up by them on a short page.
        pool = [(m.stroke, w) for m, w in zip(metrics, weights) if m.stroke_ok and m.is_cjk == cjk and m.stroke > 0]
        ref_stroke[cjk] = _weighted_median(pool) if len(pool) >= 3 else None
    usable_slants = [m.slant for m in metrics if m.slant_ok]
    ref_slant = _median(usable_slants) if len(usable_slants) >= 3 else 0.0
    # Most lines of a page are not italic, so the median slant is the scan's own skew.
    ref_slant = ref_slant if ref_slant is not None else 0.0

    out: list[StyleEstimate] = []
    for scan, m in zip(scans, metrics):
        bold, conf_b = False, 0.5
        if m.stroke_ok and m.stroke > 0:
            ref = ref_stroke[m.is_cjk]
            if ref and ref > 0:
                ratio = m.stroke / ref
                bold = ratio >= BOLD_RATIO
                conf_b = float(np.clip(0.5 + abs(ratio - BOLD_RATIO) * 1.2, 0.5, 0.97))
            else:
                limit = ABS_BOLD_CJK if m.is_cjk else ABS_BOLD_LATIN
                bold = m.stroke >= limit
                conf_b = 0.55  # no page context: a guess
        lean = m.slant - ref_slant
        italic = m.slant_ok and lean >= ITALIC_SHEAR_MIN and m.gain >= ITALIC_GAIN_MIN
        conf_i = float(np.clip(0.5 + (m.gain - 1.0) * 3.0, 0.5, 0.97)) if italic else float(
            np.clip(0.97 - max(0.0, lean) * 3.0, 0.5, 0.97)
        )
        out.append(
            StyleEstimate(
                size_px=scan.em / scan.scale,
                stroke=m.stroke,
                slant=lean,
                bold=bold,
                bold_confidence=conf_b,
                italic=bool(italic),
                italic_confidence=conf_i,
            )
        )
    return out


def regularize_sizes(sizes: list[float], reliable: list[bool], pull_reliable: float = 0.06, pull_unreliable: float = 0.18) -> list[float]:
    """Pulls each size toward the sizes that actually recur on the page.

    A short line ("單價", five blobs) cannot be sized on its own — measured error up to
    12 % — but a page rarely has more than a few distinct sizes. Reliable lines (many
    blobs) vote; clusters with at least two members become the page's sizes, and a line
    close to one is moved onto it.
    """
    votes = sorted(sz for sz, ok in zip(sizes, reliable) if ok and sz > 0)
    clusters: list[list[float]] = []
    for v in votes:
        if clusters and v <= float(np.mean(clusters[-1])) * 1.08:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    centers = [float(np.median(c)) for c in clusters if len(c) >= 2]
    out = []
    for sz, ok in zip(sizes, reliable):
        best, err = sz, 1.0
        for c in centers:
            e = abs(sz / c - 1.0) if c > 0 else 1.0
            if e < err:
                best, err = c, e
        limit = pull_reliable if ok else pull_unreliable
        out.append(best if err <= limit else sz)
    return out
