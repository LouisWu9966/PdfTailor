"""Recognising geometric symbols by shape, without OCR.

OCR engines read ■ as 圖, □ as ``[`` or ``O`` and ● as ``@``, and they drop 「」 and 『』
now and then. These shapes are simple and distinctive, so they are classified directly
from the ink blob instead.

A blob alone is not enough, though. Measured on Noto Sans CJK TC:

* the inner square of 回 / 国 / 園 is a *separate* blob, which leaves the outer frame
  looking exactly like □ — so a candidate with another blob inside it is part of a glyph;
* 「 and F, P, r look alike as shapes. They differ in *where they sit*: 「 and 『 hang
  well above the baseline (bottom at about −0.2 em), letters sit on it (0.0); 」 and 』
  start below cap height and dip under the baseline;
* a small dot is far more often a full stop, a colon or the dot of an i than a bullet,
  so small marks only count when they stand alone.

Thresholds come from ``tools/calibrate_symbols.py``, which re-measures them on any font
— worth running on the fonts of documents that matter.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .imaging import Component, component_mask


@dataclass(frozen=True)
class SymbolMatch:
    char: str
    confidence: float
    kind: str  # "square", "circle", "triangle", "diamond", "bracket"


@dataclass(frozen=True)
class Shape:
    w: int
    h: int
    asp: float
    fill: float
    holes: int
    hole_ratio: float  # area of the largest hole / bounding box
    circularity: float  # of the outer contour: 4*pi*A / P^2
    rectangularity: float  # outer contour area / bounding box
    triangularity: float  # outer contour area / smallest enclosing triangle


def shape_of(mask: np.ndarray) -> Shape | None:
    h, w = mask.shape
    if h < 4 or w < 4:
        return None
    u8 = mask.astype(np.uint8)
    cnts, hier = cv2.findContours(u8, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts or hier is None:
        return None
    outer = [c for c, hh in zip(cnts, hier[0]) if hh[3] < 0]
    if not outer:
        return None
    c0 = max(outer, key=cv2.contourArea)
    a_out = cv2.contourArea(c0)
    if a_out <= 0:
        return None
    holes = [cv2.contourArea(c) for c, hh in zip(cnts, hier[0]) if hh[3] >= 0]
    holes = [a for a in holes if a >= 0.01 * w * h]  # specks of noise are not holes
    peri = cv2.arcLength(c0, True)
    circ = 4 * np.pi * a_out / (peri * peri) if peri > 0 else 0.0
    try:
        tri_area = float(cv2.minEnclosingTriangle(c0)[0])
    except cv2.error:
        tri_area = 0.0
    return Shape(
        w=w,
        h=h,
        asp=w / h,
        fill=float(mask.sum()) / (w * h),
        holes=len(holes),
        hole_ratio=(max(holes) / (w * h)) if holes else 0.0,
        circularity=circ,
        rectangularity=a_out / (w * h),
        triangularity=a_out / tri_area if tri_area > 0 else 0.0,
    )


def _bars(mask: np.ndarray) -> tuple[bool, bool, bool, bool]:
    """Which sides carry a long straight stroke: (top, bottom, left, right)."""
    h, w = mask.shape
    rows, cols = mask.mean(axis=1), mask.mean(axis=0)
    t, s = max(1, int(round(h * 0.3))), max(1, int(round(w * 0.3)))
    return (
        bool(rows[:t].max() >= 0.75),
        bool(rows[-t:].max() >= 0.75),
        bool(cols[:s].max() >= 0.75),
        bool(cols[-s:].max() >= 0.75),
    )


def _quadrants(mask: np.ndarray) -> tuple[float, float, float, float]:
    h, w = mask.shape
    hh, ww = h // 2, w // 2
    return (
        float(mask[:hh, :ww].mean()),
        float(mask[:hh, ww:].mean()),
        float(mask[hh:, :ww].mean()),
        float(mask[hh:, ww:].mean()),
    )


def classify_shape(mask: np.ndarray, em: float, top_em: float | None, bottom_em: float | None) -> SymbolMatch | None:
    """Classifies one blob.

    ``top_em`` / ``bottom_em`` are the blob's top and bottom relative to the line's
    baseline, in ems (negative = above). They are needed to tell corner brackets from
    letters; without them brackets are not reported.
    """
    sh = shape_of(mask)
    if sh is None or em <= 0:
        return None
    size = sh.h / em

    # ---- triangles first: a hollow triangle must not be mistaken for a diamond --------
    if 1.0 <= sh.asp <= 1.35 and sh.triangularity >= 0.88 and size >= 0.5:
        rows = mask.mean(axis=1)
        k = max(1, int(round(sh.h * 0.2)))
        # The flat side is a full-width row; the apex end never gets close. Comparing the
        # strongest row at each end works for hollow outlines too, where averaging fails.
        top, bottom = float(rows[:k].max()), float(rows[-k:].max())
        filled = sh.holes == 0 and 0.42 <= sh.fill <= 0.62
        hollow = sh.holes >= 1 and sh.fill <= 0.25 and 0.25 <= sh.hole_ratio <= 0.55
        if filled or hollow:
            if bottom > top + 0.4:
                return SymbolMatch("▲" if filled else "△", 0.82, "triangle")
            if top > bottom + 0.4:
                return SymbolMatch("▼" if filled else "▽", 0.82, "triangle")

    # ---- squares, circles, diamonds ---------------------------------------------------
    if 0.85 <= sh.asp <= 1.18:
        if sh.holes == 0 and sh.fill >= 0.90 and sh.rectangularity >= 0.9:
            if 0.5 <= size <= 1.15:
                return SymbolMatch("■", 0.95, "square")
            if 0.15 <= size < 0.5:
                return SymbolMatch("▪", 0.9, "square")
        if sh.holes >= 1 and sh.hole_ratio >= 0.70 and sh.fill <= 0.5 and sh.rectangularity >= 0.88:
            if 0.5 <= size <= 1.15:
                return SymbolMatch("□", 0.9, "square")
            if 0.15 <= size < 0.5:
                return SymbolMatch("▫", 0.85, "square")
        if sh.holes == 0 and 0.68 <= sh.fill <= 0.88 and sh.circularity >= 0.82:
            if size >= 0.5:
                return SymbolMatch("●", 0.92, "circle")
            if 0.15 <= size < 0.5:
                return SymbolMatch("•", 0.8, "circle")
        if (
            sh.holes >= 1
            and sh.circularity >= 0.82
            and sh.hole_ratio >= 0.55
            and sh.fill <= 0.32
            and size >= 0.8
            and sh.asp >= 0.92
        ):
            return SymbolMatch("○", 0.7, "circle")  # a capital O is smaller and narrower
        if sh.holes == 0 and 0.42 <= sh.fill <= 0.62 and sh.circularity >= 0.7 and sh.triangularity < 0.8 and sh.rectangularity < 0.7 and size >= 0.5:
            return SymbolMatch("◆", 0.8, "diamond")
        if sh.holes >= 1 and sh.fill <= 0.22 and 0.30 <= sh.hole_ratio <= 0.55 and sh.triangularity < 0.8 and sh.rectangularity < 0.7 and size >= 0.5:
            return SymbolMatch("◇", 0.75, "diamond")

    # ---- corner brackets 「 」 『 』 ---------------------------------------------------
    if top_em is not None and bottom_em is not None and 0.28 <= sh.asp <= 0.85 and 0.4 <= size <= 0.95 and sh.fill <= 0.55 and sh.holes <= 1:
        top, bottom, left, right = _bars(mask)
        tl, tr, bl, br = _quadrants(mask)
        double = sh.holes == 1
        corner = _corner_fill(mask)
        # F and P have a crossbar through the middle; a bracket has only its one stroke.
        h, w = mask.shape
        mid = mask[int(h * 0.38) : int(h * 0.62), :].mean(axis=1).max() if h >= 8 else 1.0
        no_crossbar = mid <= (0.65 if double else 0.5)

        need = 0.5 if double else 0.62  # nested strokes leave the corner less solid; thin serif strokes too
        stray = 0.20 if double else 0.12  # the inner stroke of 『 』 spills into the far quadrant
        # 「 hangs above the baseline; letters like F, P, r, T sit on it.
        if top and left and not bottom and not right and br < stray and no_crossbar and corner[0] >= need and bottom_em <= -0.10:
            return SymbolMatch("『" if double else "「", 0.8, "bracket")
        # 」 starts below cap height and dips under the baseline; J, 4 reach cap height.
        # The margin is deliberately loose: em and baseline are themselves estimates.
        if bottom and right and not top and not left and tl < stray and no_crossbar and corner[1] >= need and top_em >= -0.70 and bottom_em >= 0.0:
            return SymbolMatch("』" if double else "」", 0.8, "bracket")
    return None


def _corner_fill(mask: np.ndarray) -> tuple[float, float]:
    """Ink density in the top-left and bottom-right corners (a fifth of the blob each).

    A bracket meets at a sharp right angle, so its corner is solid; a curved letter such
    as J or a round hook leaves the corner partly empty.
    """
    h, w = mask.shape
    ch, cw = max(2, int(h * 0.2)), max(2, int(w * 0.28))
    return float(mask[:ch, :cw].mean()), float(mask[-ch:, -cw:].mean())


def _contains_other(c: Component, comps: list[Component]) -> bool:
    """True when another blob sits inside ``c``'s frame — i.e. ``c`` is part of a glyph."""
    mx, my = c.w * 0.12, c.h * 0.12
    for o in comps:
        if o.label == c.label:
            continue
        if o.x >= c.x + mx and o.right <= c.right - mx and o.y >= c.y + my and o.bottom <= c.bottom - my:
            return True
    return False


def detect_line_symbols(
    comps: list[Component],
    labels: np.ndarray,
    em: float,
    baseline: float,
) -> list[tuple[Component, SymbolMatch]]:
    """Finds the symbol blobs of one text line.

    ``comps`` must be sorted left to right; ``baseline`` is a y coordinate in the same
    crop as the components.
    """
    found: list[tuple[Component, SymbolMatch]] = []
    for i, c in enumerate(comps):
        if _contains_other(c, comps):
            continue
        mask = component_mask(labels, c)
        top_em = (c.y - baseline) / em
        bottom_em = (c.bottom - baseline) / em
        m = classify_shape(mask, em, top_em, bottom_em)
        if m is None:
            continue

        # A small dot is a full stop, a colon, the dot of an i or a decimal point far more
        # often than a bullet. A bullet is a list marker: first on the line, standing alone.
        if m.char in ("•", "▪", "▫"):
            right_gap = comps[i + 1].x - c.right if i + 1 < len(comps) else 1e9
            centre = ((c.y + c.bottom) / 2 - baseline) / em
            midline = -0.62 <= centre <= -0.12  # bullets float; full stops sit on the baseline
            if not (i == 0 and right_gap >= 0.3 * em and midline):
                continue
        found.append((c, m))
    return found
