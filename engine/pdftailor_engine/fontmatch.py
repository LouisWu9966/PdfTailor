"""Guessing which installed font a scanned line was set in.

A scan carries no font names, but the letters themselves are evidence. For each
installed candidate family the line's own OCR text is rendered, scaled onto the scanned
ink box and compared with the scanned ink (correlation of slightly blurred masks), and
the candidate's natural width-to-height ratio is compared with the scan's. The family
with the best combined score wins if it beats the runner-up clearly enough; otherwise
the block keeps ``family=None`` and the resolver falls back to the defaults.

Shape correlation separates 黑體/明體/楷體 and sans/serif Latin; the aspect term
separates wide faces (Verdana, DejaVu) from narrow ones (Arial, Calibri), which a
shape-only score after rescaling cannot see.

Once a family is chosen the size can be re-derived from the ink height: the font's
own ink extent for this text, in ems, divided into the measured height in points.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .fonts import CJK_SANS, CJK_SERIF, FontIndex, is_cjk

LATIN_CANDIDATES = [
    "Calibri", "Arial", "Times New Roman", "Cambria", "Verdana", "Tahoma", "Segoe UI", "Georgia",
    "Century Gothic", "Garamond", "Book Antiqua", "Courier New", "Consolas",
    "Liberation Sans", "Liberation Serif", "Liberation Mono", "DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono",
]
CJK_CANDIDATES = CJK_SANS + CJK_SERIF + ["DFKai-SB", "BiauKai", "KaiTi"]

RENDER_H = 48  # px, height the comparison is done at
ASPECT_WEIGHT = 2.5
MIN_MARGIN = 0.015  # best must beat runner-up by this much


@dataclass
class FontGuess:
    family: str
    score: float
    margin: float
    size: float | None  # refined size in pt, or None
    ranking: list[tuple[str, float]]


def _ink_bbox(mask: np.ndarray):
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def _blur(a: np.ndarray, k: int = 3) -> np.ndarray:
    import cv2

    return cv2.GaussianBlur(a.astype(np.float32), (2 * k + 1, 2 * k + 1), 0)


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    d = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d > 0 else 0.0


def _render(face, text: str, px: int):
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.truetype(face.path, size=px, index=face.index)
    except Exception:
        return None
    w = int(px * (len(text) + 2) * 1.2) + 20
    img = Image.new("L", (w, int(px * 2.2)), 0)
    ImageDraw.Draw(img).text((10, int(px * 0.5)), text, fill=255, font=font)
    return np.asarray(img) > 100


def match_font(
    ink: np.ndarray,
    text: str,
    bold: bool,
    italic: bool,
    fonts: FontIndex,
    scale: float,
    candidates: list[str] | None = None,
) -> FontGuess | None:
    """``ink``: boolean mask of the scanned line (any crop that contains just this line).

    ``scale`` is pixels per point of ``ink``. Returns None when the text is too short
    to judge or no candidate is installed.
    """
    import cv2

    text = text.strip()
    glyphs = [c for c in text if not c.isspace()]
    cjk = sum(is_cjk(c) for c in glyphs)
    if len(glyphs) < (4 if cjk * 2 >= len(glyphs) else 6):
        return None
    bb = _ink_bbox(ink)
    if bb is None:
        return None
    x0, y0, x1, y1 = bb
    scan = ink[y0:y1, x0:x1]
    sh, sw = scan.shape
    if sh < 6 or sw < 12:
        return None
    scan_aspect = sw / sh
    tw = max(8, int(round(RENDER_H * scan_aspect)))
    scan_n = cv2.resize(scan.astype(np.uint8) * 255, (tw, RENDER_H), interpolation=cv2.INTER_AREA) > 100
    scan_b = _blur(scan_n)

    pool = candidates or (CJK_CANDIDATES if cjk * 2 >= len(glyphs) else LATIN_CANDIDATES)
    seen: set[str] = set()
    scored: list[tuple[str, float, float]] = []  # family, score, em height of ink
    for fam in pool:
        face = fonts.pick_face(fam, bold, italic)
        if face is None or face.id in seen:
            continue
        seen.add(face.id)
        if sum(fonts.covers(face, c) for c in glyphs) < 0.95 * len(glyphs):
            continue
        px = 64
        r = _render(face, text, px)
        if r is None:
            continue
        rb = _ink_bbox(r)
        if rb is None:
            continue
        rx0, ry0, rx1, ry1 = rb
        cand = r[ry0:ry1, rx0:rx1]
        ch, cw = cand.shape
        cand_aspect = cw / ch
        cand_n = cv2.resize(cand.astype(np.uint8) * 255, (tw, RENDER_H), interpolation=cv2.INTER_AREA) > 100
        shape = _corr(scan_b, _blur(cand_n))
        aspect_err = abs(np.log(cand_aspect / scan_aspect))
        scored.append((face.family, shape - ASPECT_WEIGHT * aspect_err, ch / px))
    if not scored:
        return None
    scored.sort(key=lambda t: -t[1])
    best = scored[0]
    margin = best[1] - scored[1][1] if len(scored) > 1 else 1.0
    size = (sh / scale) / best[2] if best[2] > 0 else None
    return FontGuess(best[0], best[1], margin, size, [(f, round(s, 4)) for f, s, _ in scored])
