"""Synthetic scans with known ground truth, for measuring OCR and style analysis.

Text is rendered with real fonts (Noto Sans CJK TC in several weights, DejaVu for Latin
italics) and then degraded the way a scanner does: blur, noise, a slight skew and JPEG
compression. Because the truth is known, accuracy can be *measured* instead of guessed.
"""

from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

NOTO = "/usr/share/fonts/opentype/noto/NotoSansCJK-{w}.ttc"
NOTO_SERIF = "/usr/share/fonts/opentype/noto/NotoSerifCJK-{w}.ttc"
DEJAVU = "/usr/share/fonts/truetype/dejavu/{n}.ttf"
TC_INDEX = 3  # "Noto Sans CJK TC" inside the .ttc


def font_path(family: str, weight: str) -> tuple[str, int]:
    if family == "dejavu":
        return DEJAVU.format(n={"Regular": "DejaVuSans", "Bold": "DejaVuSans-Bold"}[weight]), 0
    if family == "dejavu-serif":
        return DEJAVU.format(n={"Regular": "DejaVuSerif", "Bold": "DejaVuSerif-Bold"}[weight]), 0
    if family == "noto-serif":
        return NOTO_SERIF.format(w=weight), TC_INDEX
    return NOTO.format(w=weight), TC_INDEX


@dataclass
class Style:
    family: str = "noto"
    weight: str = "Regular"  # Light | Regular | Medium | Bold | Black
    size_px: int = 40
    shear: float = 0.0  # synthetic italic: horizontal shear, tan(angle)
    color: tuple[int, int, int] = (20, 20, 20)


def render_line(text: str, style: Style, pad: int = 12, bg: tuple[int, int, int] = (250, 250, 248)) -> Image.Image:
    path, idx = font_path(style.family, style.weight)
    font = ImageFont.truetype(path, style.size_px, index=idx)
    l, t, r, b = font.getbbox(text)
    w = r - l + 2 * pad + int(abs(style.shear) * style.size_px)
    h = style.size_px * 2
    img = Image.new("RGB", (w, h), bg)
    ImageDraw.Draw(img).text((pad - l, style.size_px // 2), text, font=font, fill=style.color)
    if style.shear:
        # Shear about the baseline so the bottom of the glyphs stays put. PIL's AFFINE maps
        # output -> input, so a positive c pulls upper rows from the left: the text leans
        # right, like a real italic.
        base = style.size_px // 2 + font.getmetrics()[0]
        c = style.shear
        img = img.transform(
            img.size,
            Image.AFFINE,
            (1, c, -c * base, 0, 1, 0),
            resample=Image.BICUBIC,
            fillcolor=bg,
        )
    return img


def degrade(
    img: Image.Image,
    *,
    seed: int = 0,
    blur: float = 0.6,
    noise: float = 5.0,
    skew_deg: float = 0.0,
    jpeg: int | None = 78,
    scale: float = 1.0,
) -> Image.Image:
    rng = np.random.default_rng(seed)
    bg = img.getpixel((0, 0))
    if skew_deg:
        img = img.rotate(skew_deg, resample=Image.BICUBIC, expand=True, fillcolor=bg)
    if scale != 1.0:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    arr = np.asarray(img).astype(np.float32)
    if noise:
        arr += rng.normal(0, noise, arr.shape)
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    if jpeg:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=jpeg)
        img = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    return img


def compose_page(lines: list[tuple[str, Style]], width: int = 1600, line_gap: float = 1.9, margin: int = 60) -> tuple[Image.Image, list[tuple[int, int, int, int]]]:
    """Stacks rendered lines on a page. Returns the page and each line's ink box."""
    bg = (250, 250, 248)
    rendered = [render_line(t, s) for t, s in lines]
    height = margin * 2 + int(sum(s.size_px * line_gap for _, s in lines))
    page = Image.new("RGB", (width, height), bg)
    y = margin
    boxes = []
    for (text, style), im in zip(lines, rendered):
        page.paste(im, (margin, y))
        boxes.append((margin, y, margin + im.width, y + im.height))
        y += int(style.size_px * line_gap)
    return page, boxes
