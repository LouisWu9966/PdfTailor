"""Writing edited blocks back into a PDF.

For every page with at least one modified block:

1. **Remove the old text.**
   * Real text (``source == "text"``): the show-text strings inside the block's original
     bounds are emptied in the content stream (``redact.redact_page``). Nothing else in
     the stream changes.
   * Scanned text (``source == "ocr"``): the letters are pixels in an image, which is
     never touched. Instead a small patch is painted over them: the page is rendered,
     the ink of the old line is masked (long thin rules such as table borders are kept
     out of the mask) and in-painted from the paper around it, so the patch carries the
     paper's own tone and noise rather than a flat white box. Any invisible OCR text
     layer in the same place is redacted too, so searching does not find the old words.
2. **Draw the new text** on an overlay (reportlab) that is merged over the page as a
   form XObject (pikepdf ``add_overlay``). Text is placed by its *ink*: the new line's
   ink box lands where the old line's ink box was, using the glyph bounds of the font
   actually drawn, so CJK and Latin lines both sit on the right baseline.
3. **Save without re-encoding**: ``stream_decode_level=none`` keeps every untouched
   stream — the scan images above all — byte for byte.

Coordinates in :class:`Edit` are display space (pt, top-left origin, rotation
applied); everything is mapped to user space through :class:`PageGeometry`, so rotated
pages and pages with a shifted CropBox come out right.
"""

from __future__ import annotations

import io
import math
import os
import tempfile
from dataclasses import dataclass, field

import numpy as np
import pikepdf

from .fonts import Embedder, FontIndex, ResolvedFont, Segment, default_index
from .geometry import PageGeometry, Rect
from .models import Edit, Run, TextStyle
from .pdfio import PdfDoc
from .redact import _inherited, flate_stream, redact_page

SYNTH_ITALIC_SHEAR = math.tan(math.radians(12))
SYNTH_BOLD_STROKE = 0.035  # of the font size
MIN_HSCALE = 0.85  # squeeze a too-long line at most this much before letting it overflow
PATCH_DPI = 300


@dataclass
class ExportReport:
    out_path: str
    pages_changed: list[int] = field(default_factory=list)
    blocks_written: int = 0
    runs_removed: int = 0
    notes: list[dict] = field(default_factory=list)  # {"block": id, "page": n, "note": str}

    def note(self, edit: Edit, msg: str) -> None:
        self.notes.append({"block": edit.id, "page": edit.page, "note": msg})

    def to_json(self) -> dict:
        return {
            "outPath": self.out_path,
            "pagesChanged": self.pages_changed,
            "blocksWritten": self.blocks_written,
            "runsRemoved": self.runs_removed,
            "notes": self.notes,
        }


# --------------------------------------------------------------------------------------
# drawing helpers
# --------------------------------------------------------------------------------------
class _Frame:
    """A reportlab transform where (u, v) means display point (dx + u, dy - v).

    u runs along the display's x axis, v points up the display. Text drawn at (0, 0) in
    this frame is upright on screen whatever the page's /Rotate is.
    """

    def __init__(self, canvas, geom: PageGeometry, dx: float, dy: float):
        self.c = canvas
        self.geom = geom
        self.dx, self.dy = dx, dy

    def __enter__(self):
        ux, uy = self.geom.display_to_user(self.dx, self.dy)
        a, b = self.geom.baseline_matrix()
        self.c.saveState()
        self.c.translate(ux, uy)
        self.c.rotate(math.degrees(math.atan2(b, a)))
        return self.c

    def __exit__(self, *exc):
        self.c.restoreState()


def _style_runs(edit: Edit) -> list[Run]:
    """The edit as runs: per-run styles when they still spell the text, else one run."""
    if edit.runs and "".join(r.text for r in edit.runs) == edit.text and not edit.style_dirty:
        return edit.runs
    return [Run(edit.text, edit.style)]


@dataclass
class _Piece:
    seg: Segment
    style: TextStyle
    width: float  # unscaled, pt


@dataclass
class _Layout:
    pieces: list[_Piece]
    ink_x0: float  # ink left relative to the pen start, pt
    ink_x1: float  # ink right relative to the pen start, pt
    ymin: float  # lowest ink, pt (negative = below baseline)
    ymax: float


def _layout(edit: Edit, fonts: FontIndex, emb: Embedder, text_runs: list[Run], resolved: list[ResolvedFont]) -> _Layout:
    pieces: list[_Piece] = []
    for run, rf in zip(text_runs, resolved):
        for seg in rf.segments:
            pieces.append(_Piece(seg, run.style, emb.width(seg.face, seg.text, run.style.size)))
    pen = 0.0
    ink_x0 = None
    ink_x1 = 0.0
    ymin, ymax = 0.0, 0.0
    for p in pieces:
        size = p.style.size
        for ch in p.seg.text:
            x0, y0, x1, y1, adv = emb.glyph_bounds(p.seg.face, ch)
            if not ch.isspace() and (x1 > x0 or y1 > y0):
                if ink_x0 is None:
                    ink_x0 = pen + x0 * size
                ink_x1 = pen + x1 * size
                ymin = min(ymin, y0 * size)
                ymax = max(ymax, y1 * size)
            pen += adv * size
    return _Layout(pieces, ink_x0 or 0.0, ink_x1, ymin, ymax)


def _resolve_runs(fonts: FontIndex, runs: list[Run], serif: bool | None) -> list[ResolvedFont]:
    return [fonts.resolve(r.style, r.text, serif) for r in runs]


# --------------------------------------------------------------------------------------
# erasing scanned text
# --------------------------------------------------------------------------------------
def _erase_rect(edit: Edit) -> Rect:
    b = edit.original_bounds
    h = max(b.h, 1.0)
    return b.inflate(max(0.8, 0.10 * h), max(0.6, 0.14 * h))


def _inpaint_patch(page_rgb: np.ndarray, scale: float, rect: Rect, em_pt: float):
    """In-painted pixels for ``rect`` (display pt) or None if the rect is off the page."""
    import cv2
    from PIL import Image

    from .analysis.imaging import distance_map, estimate_background, find_components, ink_mask

    H, W = page_rgb.shape[:2]
    x0, y0 = int(math.floor(rect.x * scale)), int(math.floor(rect.y * scale))
    x1, y1 = int(math.ceil(rect.right * scale)), int(math.ceil(rect.bottom * scale))
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None, None
    m = int(round(max(4, 0.6 * (y1 - y0))))
    cx0, cy0, cx1, cy1 = max(0, x0 - m), max(0, y0 - m), min(W, x1 + m), min(H, y1 + m)
    crop = np.ascontiguousarray(page_rgb[cy0:cy1, cx0:cx1])
    bg, _spread = estimate_background(crop)
    mask, _bg, _thr = ink_mask(crop, bg)
    inner = np.zeros_like(mask, dtype=bool)
    inner[y0 - cy0 : y1 - cy0, x0 - cx0 : x1 - cx0] = True
    mask = mask & inner

    # Keep ruling lines out of the mask: a table border under or through the line is page
    # furniture, not part of the text.
    em_px = max(2.0, em_pt * scale)
    comps, labels = find_components(mask.astype(np.uint8), min_area=1)
    for c in comps:
        thin_h = c.w >= 3.0 * em_px and c.h <= 0.15 * em_px
        thin_v = c.h >= 1.6 * em_px and c.w <= 0.15 * em_px and c.h >= 0.9 * (y1 - y0)
        if thin_h or thin_v:
            mask[labels == c.label] = False

    # The core mask misses the soft edge a scanner's blur leaves around every stroke,
    # which would survive as a faint ghost. Add every pixel that stands out from the
    # paper's own noise, as long as it touches a stroke.
    dist = distance_map(crop, bg)
    paper = dist[(dist < 28) & ~inner]
    noise = float(np.percentile(paper, 95)) if paper.size > 50 else 8.0
    halo = dist > max(8.0, 1.4 * noise)
    reach = max(2, int(round(0.08 * em_px)))
    near_ink = cv2.dilate(mask.astype(np.uint8), np.ones((2 * reach + 1, 2 * reach + 1), np.uint8)) > 0
    mask = (mask | (halo & near_ink)) & inner

    mask_u8 = mask.astype(np.uint8) * 255
    mask_u8 = cv2.dilate(mask_u8, np.ones((3, 3), np.uint8), iterations=max(1, int(round(scale * 0.4))))
    mask_u8[~inner] = 0
    radius = max(3, int(round(0.15 * em_px)))
    bgr = cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)
    out = cv2.inpaint(bgr, mask_u8, radius, cv2.INPAINT_TELEA)
    out = cv2.cvtColor(out, cv2.COLOR_BGR2RGB)[y0 - cy0 : y1 - cy0, x0 - cx0 : x1 - cx0]
    # The patch covers exactly the pixels it was cut from.
    exact = Rect(x0 / scale, y0 / scale, (x1 - x0) / scale, (y1 - y0) / scale)
    return Image.fromarray(out), exact


def _paint_patch(c, geom: PageGeometry, img, rect: Rect) -> None:
    from reportlab.lib.utils import ImageReader

    with _Frame(c, geom, rect.x, rect.bottom):
        c.drawImage(ImageReader(img), 0, 0, width=rect.w, height=rect.h, mask=None)


def _paint_solid(c, geom: PageGeometry, rect: Rect, rgb) -> None:
    with _Frame(c, geom, rect.x, rect.bottom):
        c.setFillColorRGB(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
        c.setStrokeColorRGB(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
        c.setLineWidth(0)
        c.rect(0, 0, rect.w, rect.h, stroke=0, fill=1)


# --------------------------------------------------------------------------------------
# drawing new text
# --------------------------------------------------------------------------------------
def _room(edit: Edit, others: list[Edit], geom: PageGeometry) -> tuple[float, float]:
    """How far the line may grow to the left and right before it runs into a neighbour
    on the same row (or the page margin), in display pt."""
    b = edit.bounds
    gap = 0.5 * edit.style.size
    left, right = 18.0, geom.display_w - 18.0
    for o in others:
        if o is edit:
            continue
        ob = o.original_bounds if not o.modified else o.bounds
        overlap = min(b.bottom, ob.bottom) - max(b.y, ob.y)
        if overlap <= 0.5 * min(b.h, ob.h):
            continue
        if ob.x >= b.cx:
            right = min(right, ob.x - gap)
        elif ob.right <= b.cx:
            left = max(left, ob.right + gap)
    return min(left, b.x), max(right, b.right)


def _draw_text(
    c, geom: PageGeometry, edit: Edit, lay: _Layout, emb: Embedder, report: ExportReport, ref: _Layout,
    room: tuple[float, float],
) -> None:
    b = edit.bounds
    ink_w = lay.ink_x1 - lay.ink_x0
    if edit.align == "center":
        avail = 2 * min(b.cx - room[0], room[1] - b.cx)
    elif edit.align == "right":
        avail = b.right - room[0]
    else:
        avail = room[1] - b.x
    hscale = 1.0
    # A longer line simply runs on into free space, as it would in Word; it is squeezed
    # (slightly) only when it would hit the next column or the page edge.
    if ink_w > avail:
        hscale = max(MIN_HSCALE, avail / ink_w)
        if avail / ink_w < MIN_HSCALE:
            report.note(edit, "text-overflow")
    if edit.align == "center":
        start = b.cx - (lay.ink_x0 + ink_w / 2) * hscale
    elif edit.align == "right":
        start = b.right - lay.ink_x1 * hscale
    else:
        start = b.x - lay.ink_x0 * hscale
    # The baseline comes from the *original* text in the drawn font: its lowest ink sat on
    # the block's bottom edge. A line with descenders (gy) or ideographs sits higher.
    baseline = b.bottom + ref.ymin

    with _Frame(c, geom, start, baseline):
        pen = 0.0
        for p in lay.pieces:
            st = p.style
            r, g, bl = (v / 255 for v in st.color)
            t = c.beginText()
            t.setFont(emb.name(p.seg.face), st.size)
            shear = SYNTH_ITALIC_SHEAR if p.seg.synth_italic else 0.0
            t.setTextTransform(hscale, 0, shear, 1, pen * hscale, 0)
            t.setHorizScale(100.0)
            c.setFillColorRGB(r, g, bl)
            if p.seg.synth_bold:
                c.setStrokeColorRGB(r, g, bl)
                c.setLineWidth(st.size * SYNTH_BOLD_STROKE)
                t.setTextRenderMode(2)
            else:
                t.setTextRenderMode(0)
            t.textOut(p.seg.text)
            c.drawText(t)
            pen += p.width


def _stamp(pdf: pikepdf.Pdf, page: pikepdf.Page, ov_page: pikepdf.Page, left: float, bottom: float) -> None:
    """Draws ``ov_page`` over ``page`` as a form XObject, in the page's user space.

    pikepdf's add_overlay would compensate for /Rotate, but the overlay is already laid
    out in user space, so it is placed with a plain translation instead. The existing
    content is wrapped in q/Q so a graphics state it leaves behind cannot leak into the
    overlay.
    """
    form = pdf.copy_foreign(ov_page.as_form_xobject())
    # the overlay page starts at (0, 0); the target's MediaBox may not
    # Resources may be inherited from /Pages or shared with other pages: give this page
    # private (shallow) copies before adding to them.
    inherited = _inherited(page.obj, "/Resources")
    res = pikepdf.Dictionary({k: v for k, v in inherited.items()}) if inherited is not None else pikepdf.Dictionary()
    old_x = res.get("/XObject")
    xobjs = pikepdf.Dictionary({k: v for k, v in old_x.items()}) if old_x is not None else pikepdf.Dictionary()
    res["/XObject"] = xobjs
    page.obj["/Resources"] = res
    i = 0
    while pikepdf.Name(f"/PtEdit{i}") in xobjs:
        i += 1
    name = pikepdf.Name(f"/PtEdit{i}")
    xobjs[name] = form
    head = flate_stream(pdf, b"q\n")
    tail = flate_stream(pdf, b"\nQ\nq 1 0 0 1 %.4f %.4f cm %s Do Q\n" % (left, bottom, bytes(str(name), "ascii")))
    contents = page.obj.get("/Contents")
    parts = list(contents) if isinstance(contents, pikepdf.Array) else ([contents] if contents is not None else [])
    page.obj["/Contents"] = pikepdf.Array([head, *parts, tail])


# --------------------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------------------
def write_edits(
    src_path: str,
    edits: list[Edit],
    out_path: str,
    fonts: FontIndex | None = None,
    doc: PdfDoc | None = None,
) -> ExportReport:
    from reportlab.pdfgen import canvas as rl_canvas

    fonts = fonts or default_index()
    doc = doc or PdfDoc(src_path)
    report = ExportReport(out_path=os.path.abspath(out_path))
    by_page: dict[int, list[Edit]] = {}
    for e in edits:
        if e.modified:
            by_page.setdefault(e.page, []).append(e)

    emb = Embedder()
    plans: dict[int, list[tuple[Edit, _Layout, _Layout]]] = {}
    # Pass 1: resolve fonts and collect every character so each face is subset once.
    resolved_all: list[tuple[Edit, list[Run], list[ResolvedFont], list[Run], list[ResolvedFont]]] = []
    for page_no, page_edits in by_page.items():
        for e in page_edits:
            serif = None if e.style.family else False
            runs = _style_runs(e)
            rfs = _resolve_runs(fonts, runs, serif)
            ref_runs = [Run(e.original_text or e.text, e.style)]
            ref_rfs = _resolve_runs(fonts, ref_runs, serif)
            for rf in rfs + ref_rfs:
                for seg in rf.segments:
                    emb.want(seg.face, seg.text)
                for n in rf.notes:
                    report.note(e, n)
            resolved_all.append((e, runs, rfs, ref_runs, ref_rfs))
    for e, runs, rfs, ref_runs, ref_rfs in resolved_all:
        lay = _layout(e, fonts, emb, runs, rfs)
        ref = _layout(e, fonts, emb, ref_runs, ref_rfs)
        plans.setdefault(e.page, []).append((e, lay, ref))

    scale = PATCH_DPI / 72.0
    with pikepdf.open(src_path) as pdf:
        for page_no in sorted(plans):
            info = doc.pages[page_no - 1]
            geom = info.geometry
            page = pdf.pages[page_no - 1]

            # 1. remove old text strings (real text, and stale invisible OCR layers)
            regions = []
            for e, _lay, _ref in plans[page_no]:
                ur = geom.display_rect_to_user(e.original_bounds.inflate(0.5))
                regions.append((ur.left, ur.bottom, ur.right, ur.top))
            removed = redact_page(pdf, page, regions)
            report.runs_removed += removed

            # 2. overlay: patches first, then text
            buf = io.BytesIO()
            mb = geom.media
            c = rl_canvas.Canvas(buf, pagesize=(mb.width, mb.height), pageCompression=1)
            c.translate(-mb.left, -mb.bottom)
            page_rgb = None
            for e, _lay, _ref in plans[page_no]:
                mode = e.fill_mode
                if mode == "none":
                    continue
                if mode == "solid":
                    _paint_solid(c, geom, _erase_rect(e), e.fill_color)
                    continue
                if e.source == "ocr":  # "auto" on a scan: in-paint from the paper around it
                    if page_rgb is None:
                        page_rgb = doc.render(page_no, scale)
                    img, exact = _inpaint_patch(page_rgb, scale, _erase_rect(e), e.style.size)
                    if img is not None:
                        _paint_patch(c, geom, img, exact)
            page_all = [o for o in edits if o.page == page_no]
            for e, lay, ref in plans[page_no]:
                if e.text.strip():
                    _draw_text(c, geom, e, lay, emb, report, ref, _room(e, page_all, geom))
                report.blocks_written += 1
            c.showPage()
            c.save()

            with pikepdf.open(io.BytesIO(buf.getvalue())) as ov:
                _stamp(pdf, page, ov.pages[0], mb.left, mb.bottom)
            report.pages_changed.append(page_no)

        tmp = out_path + ".part"
        # compress_streams=True would make qpdf rewrite untouched streams (it strips ASCII85,
        # re-deflates); new streams are compressed when they are made instead.
        pdf.save(
            tmp,
            compress_streams=False,
            stream_decode_level=pikepdf.StreamDecodeLevel.none,
            object_stream_mode=pikepdf.ObjectStreamMode.preserve,
        )
    os.replace(tmp, out_path)
    return report
