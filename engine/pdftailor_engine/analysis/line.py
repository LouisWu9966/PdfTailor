"""Measuring one line of text from its pixels: size, baseline, symbols, colour."""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .imaging import (
    RGB,
    Component,
    estimate_background,
    find_components,
    ink_color,
    ink_mask,
)
from .symbols import SymbolMatch, detect_line_symbols

# Ink height of typical glyphs as a fraction of the em: the median ideograph, and the
# 80th-percentile Latin glyph (ascenders, capitals, descender letters). Fitted on 180
# lines of Noto Sans/Serif CJK TC and DejaVu Sans/Serif through blur, noise and JPEG; the
# residual size error is median 0 %, mean 1.8 %, 90th percentile 4.4 %.
CJK_INK_EM = 0.95
LATIN_INK_EM = 0.76
# CJK ink bottoms sit slightly below the true baseline (口 +0.06, 日 +0.07, 回 +0.08).
CJK_BASELINE_DROP = 0.06


def script_of(text: str) -> str:
    """``"cjk"``, ``"latin"`` or ``"mixed"`` from the characters of an OCR result."""
    cjk = latin = 0
    for ch in text:
        o = ord(ch)
        if 0x2E80 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF:
            cjk += 1
        elif ch.isalnum():
            latin += 1
    total = cjk + latin
    if total == 0:
        return "mixed"
    if cjk / total >= 0.6:
        return "cjk"
    if cjk / total <= 0.15:
        return "latin"
    return "mixed"


@dataclass
class LineScan:
    """Everything measured from the pixels of one text line (all in crop pixels)."""

    rgb: np.ndarray
    mask: np.ndarray
    bg: RGB
    bg_reliable: bool
    ink: RGB
    comps: list[Component]
    labels: np.ndarray
    em: float
    baseline: float
    script: str
    scale: int = 1  # coordinates are in a crop enlarged by this factor; divide to get back
    symbols: list[tuple[Component, SymbolMatch]] = field(default_factory=list)

    @property
    def text_comps(self) -> list[Component]:
        sym = {c.label for c, _ in self.symbols}
        return [c for c in self.comps if c.label not in sym]


def _ideograph_like(comps: list[Component], hmax: float) -> list[Component]:
    """Blobs that are tall *and* wide and close to square: whole ideographs, not the
    x-height letters (o, n, u) or digits that merely look boxy."""
    return [
        c
        for c in comps
        if c.h >= 0.72 * hmax and c.w >= 0.72 * hmax and 0.7 <= c.w / max(c.h, 1) <= 1.35
    ]


def count_cjk(text: str) -> int:
    return sum(1 for ch in text if 0x2E80 <= ord(ch) <= 0x9FFF or 0xF900 <= ord(ch) <= 0xFAFF)


def estimate_em_baseline(
    comps: list[Component], script: str = "mixed", em_hint: float | None = None, n_cjk: int = 0
) -> tuple[float, float]:
    """(em in px, baseline y) from component sizes and what script the line is in."""
    if not comps:
        return (em_hint or 0.0), 0.0
    hs = np.array([c.h for c in comps], dtype=float)
    hmax = float(np.percentile(hs, 95))
    main = [c for c in comps if c.h >= 0.5 * hmax] or comps
    ideo = _ideograph_like(main, hmax)

    # Whether the text really contains ideographs is known from the OCR result (engines
    # misread characters, but rarely confuse Chinese with Latin). Requiring that, rather
    # than trusting the blob shapes alone, keeps a line of capitals from being sized as if
    # it were Chinese — and keeps a "六角螺絲  1,200" row, which the engine reads as one
    # mixed line, from being sized as if it were Latin.
    use_cjk = False
    if script == "cjk":
        use_cjk = len(ideo) >= 1 if n_cjk <= 2 else len(ideo) >= 2
    elif n_cjk >= 2:
        use_cjk = len(ideo) >= 2

    if use_cjk:
        em = float(np.median([c.h for c in ideo])) / CJK_INK_EM
        ref = ideo
    else:
        em = float(np.percentile([c.h for c in main], 80)) / LATIN_INK_EM
        ref = main
    if em_hint and (len(comps) < 2 or em <= 0):
        em = em_hint

    bottoms = np.array([c.bottom for c in ref], dtype=float)
    med = float(np.median(bottoms))
    kept = bottoms[bottoms <= med + 0.12 * em]  # descenders do not set the baseline
    baseline = float(np.median(kept)) if len(kept) else med
    if use_cjk:
        baseline -= CJK_BASELINE_DROP * em
    return em, baseline


def upscale_factor(em_px: float | None) -> int:
    """Small text is analysed on an enlarged copy.

    Thin strokes and the holes of □ ○ 回 blur shut at 9 pt-ish sizes (30 px em at 240 dpi).
    Measured on Noto Sans CJK TC: symbol recall at 30 px went from 13/19 to 19/19 for the
    serif face and 17/19 to 19/19 for bold after a 2-3x cubic enlargement.
    """
    if not em_px or em_px >= 52:
        return 1
    return 2 if em_px >= 30 else 3


def scan_line(rgb: np.ndarray, text_hint: str = "", em_hint: float | None = None) -> LineScan:
    k = upscale_factor(em_hint)
    if k > 1:
        rgb = cv2.resize(rgb, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
        em_hint = (em_hint or 0) * k or None
    bg, ref = estimate_background(rgb)
    mask, bg, _ = ink_mask(rgb, bg)
    min_area = max(3, int(0.0012 * (em_hint or 30) ** 2))
    comps, labels = find_components(mask, min_area=min_area)
    script = script_of(text_hint) if text_hint else "mixed"
    em, baseline = estimate_em_baseline(comps, script, em_hint, count_cjk(text_hint))
    scan = LineScan(
        rgb=rgb,
        mask=mask,
        bg=bg,
        bg_reliable=ref >= 0.6,
        ink=ink_color(rgb, mask, bg),
        comps=comps,
        labels=labels,
        em=em,
        baseline=baseline,
        script=script,
        scale=k,
    )
    if em > 0:
        scan.symbols = detect_line_symbols(comps, labels, em, baseline)
    return scan
