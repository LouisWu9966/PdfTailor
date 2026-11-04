"""Builds PDFs with known content for tests."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pdftailor_engine.geometry import PageGeometry


@dataclass
class TextItem:
    """A line of text. ``x`` and ``y`` are in *display* space: ``y`` is the baseline,
    measured down from the top of the visible page — so a test reads the same whatever
    the page rotation is."""

    x: float
    y: float
    text: str
    font: str = "Helvetica"
    size: float = 12.0
    color: tuple[int, int, int] = (0, 0, 0)
    mode: int = 0  # text render mode: 3 = invisible


def make_text_pdf(
    path: str,
    items: list[TextItem],
    *,
    size: tuple[float, float] = (612, 792),
    rotate: int = 0,
    crop: tuple[float, float, float, float] | None = None,
    user_space_text: bool = False,
    rects: list[tuple[float, float, float, float, tuple[int, int, int]]] | None = None,
    image: tuple[Image.Image, tuple[float, float, float, float]] | None = None,
) -> str:
    """Writes a one-page PDF.

    With ``rotate`` set, text is laid out so it reads upright on the *display* (the
    common case: a landscape scan stored with /Rotate 90). ``user_space_text=True``
    instead keeps the text horizontal in user space, which on a rotated page shows up
    sideways — useful to test that such text is recognised as rotated.
    """
    geom = PageGeometry(1, (0, 0, size[0], size[1]), crop, rotate)
    c = canvas.Canvas(path, pagesize=size)
    c.setTitle("fixture")
    # Rotation is added afterwards with pikepdf: reportlab's setPageRotation also swaps
    # the MediaBox, which would put the content outside the page.
    if crop:
        c.setCropBox(crop)
    if image is not None:
        img, (x, y, w, h) = image
        c.drawImage(ImageReader(img), x, y, w, h)
    for (x, y, w, h, col) in rects or []:
        c.setFillColorRGB(col[0] / 255, col[1] / 255, col[2] / 255)
        c.rect(x, y, w, h, stroke=0, fill=1)
    for it in items:
        if user_space_text:
            ux, uy, deg = it.x, it.y, 0.0
        else:
            ux, uy = geom.display_to_user(it.x, it.y)
            bx, by = geom.baseline_matrix()
            deg = math.degrees(math.atan2(by, bx))
        c.saveState()
        c.translate(ux, uy)
        c.rotate(deg)
        t = c.beginText(0, 0)
        t.setFont(it.font, it.size)
        t.setFillColorRGB(it.color[0] / 255, it.color[1] / 255, it.color[2] / 255)
        t.setTextRenderMode(it.mode)
        t.textOut(it.text)
        c.drawText(t)
        c.restoreState()
    c.showPage()
    c.save()
    if rotate:
        set_rotation(path, rotate)
    return path


def set_rotation(path: str, rotate: int) -> None:
    import pikepdf

    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.pages[0].obj.Rotate = rotate
        pdf.save(path)


def ink_bbox(rgb: np.ndarray, region: tuple[int, int, int, int], thresh: int = 90) -> tuple[int, int, int, int] | None:
    """Bounding box of non-white pixels inside ``region`` (x0, y0, x1, y1), in pixels."""
    x0, y0, x1, y1 = region
    sub = rgb[max(0, y0) : y1, max(0, x0) : x1].astype(int)
    if sub.size == 0:
        return None
    dist = 255 * 3 - sub.sum(axis=2)
    ys, xs = np.nonzero(dist > thresh)
    if len(xs) == 0:
        return None
    return (x0 + int(xs.min()), y0 + int(ys.min()), x0 + int(xs.max()) + 1, y0 + int(ys.max()) + 1)
