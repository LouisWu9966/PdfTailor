"""A synthetic scanned PDF with known ground truth: the page is one image, so there is no
text layer — exactly the situation the editor has to cope with."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from scangen import Style, degrade, render_line

DPI = 200
PX_PER_PT = DPI / 72.0
PAGE_W_PT, PAGE_H_PT = 595.0, 842.0  # A4
PAGE_W, PAGE_H = int(PAGE_W_PT * PX_PER_PT), int(PAGE_H_PT * PX_PER_PT)


@dataclass
class Truth:
    text: str
    style: Style
    x_pt: float  # left of the ink, display points
    y_pt: float  # top of the ink
    w_pt: float
    h_pt: float
    bold: bool = False
    italic: bool = False

    @property
    def size_pt(self) -> float:
        return self.style.size_px / PX_PER_PT


def ink_box(img: Image.Image, bg=(250, 250, 248), thresh: int = 80):
    a = np.asarray(img).astype(int)
    d = np.abs(a - np.array(bg)).sum(axis=2)
    ys, xs = np.nonzero(d > thresh)
    if len(xs) == 0:
        return None
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def build_scan(
    items: list[tuple[float, float, str, Style]],
    *,
    chart: bool = True,
    seed: int = 3,
    degrade_kw: dict | None = None,
) -> tuple[Image.Image, list[Truth]]:
    """``items``: (x_pt, y_pt of the line's top, text, style). Returns page + truth."""
    bg = (250, 250, 248)
    page = Image.new("RGB", (PAGE_W, PAGE_H), bg)
    truth: list[Truth] = []
    for x_pt, y_pt, text, style in items:
        line = render_line(text, style, pad=6)
        ib = ink_box(line)
        if ib is None:
            continue
        px, py = int(x_pt * PX_PER_PT), int(y_pt * PX_PER_PT)
        page.paste(line, (px, py))
        x0, y0, x1, y1 = ib
        truth.append(
            Truth(
                text=text,
                style=style,
                x_pt=(px + x0) / PX_PER_PT,
                y_pt=(py + y0) / PX_PER_PT,
                w_pt=(x1 - x0) / PX_PER_PT,
                h_pt=(y1 - y0) / PX_PER_PT,
                bold=style.weight in ("Bold", "Black"),
                italic=style.shear > 0.1,
            )
        )
    if chart:
        d = ImageDraw.Draw(page)
        cx0, cy0, cx1, cy1 = int(70 * PX_PER_PT), int(560 * PX_PER_PT), int(520 * PX_PER_PT), int(760 * PX_PER_PT)
        d.rectangle([cx0, cy0, cx1, cy1], outline=(120, 130, 150), width=3)
        for i, (hgt, col) in enumerate([(90, (40, 184, 216)), (140, (139, 92, 246)), (60, (101, 230, 173)), (110, (232, 137, 44))]):
            bx = cx0 + 50 + i * 160
            d.rectangle([bx, cy1 - 10 - hgt, bx + 90, cy1 - 10], fill=col)
    page = degrade(page, seed=seed, **(degrade_kw if degrade_kw is not None else dict(blur=0.6, noise=5, jpeg=80, skew_deg=0.0)))
    return page, truth


def write_scan_pdf(path: str, page: Image.Image) -> str:
    c = canvas.Canvas(path, pagesize=(PAGE_W_PT, PAGE_H_PT))
    c.setTitle("scan fixture")
    c.drawImage(ImageReader(page), 0, 0, PAGE_W_PT, PAGE_H_PT)
    c.showPage()
    c.save()
    return path
