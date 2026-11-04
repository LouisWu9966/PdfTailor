"""High-level operations the shell calls: open, analyse, preview, export.

One :class:`Session` holds one open document. Everything that crosses the process
boundary is plain JSON-able data (dicts, lists, file paths); pages are handed over as
PNG files in the session's temp folder rather than as bytes on the pipe.

Preview and export share one code path — a preview *is* an export to a temp file,
rendered — so what the user approves is exactly what gets written.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from collections import Counter
from dataclasses import replace
from typing import Any, Callable

import numpy as np

from .analysis.imaging import ink_mask
from .fontmatch import MIN_MARGIN, match_font
from .fonts import FontIndex, default_index, is_cjk
from .geometry import Rect
from .models import Block, Edit, Run
from .ocr.base import OcrEngine, OcrLine, OcrUnavailable, ExternalOcr
from .ocr.tesseract import TesseractOcr, find_tesseract
from .pdfio import PdfDoc
from .scanpage import analyze_scanned_page
from .textlayer import build_blocks, read_glyphs
from .writer import write_edits

ANALYZE_DPI = 300
WORD_DPI = 150  # page render used to find table rules and cell shading
PREVIEW_DPI = 110
COMMON_SIZES = [6, 6.5, 7, 7.5, 8, 9, 10, 10.5, 11, 12, 13, 14, 15, 16, 18, 20, 22, 24, 26, 28, 32, 36, 40, 44, 48, 54, 60, 66, 72]
SNAP_TOLERANCE = 0.035

Progress = Callable[[str, float], bool]  # (stage, 0..1) -> keep going?


class Cancelled(Exception):
    pass


class EngineError(Exception):
    """An error meant for the user; the message is shown as is."""


def snap_size(size: float) -> float:
    best = min(COMMON_SIZES, key=lambda c: abs(c - size))
    return float(best) if abs(best / size - 1) <= SNAP_TOLERANCE else round(size * 2) / 2


def _script(text: str) -> str:
    g = [c for c in text if not c.isspace()]
    return "cjk" if g and sum(is_cjk(c) for c in g) * 2 >= len(g) else "latin"


class Session:
    def __init__(self, fonts: FontIndex | None = None, workdir: str | None = None):
        self._fonts = fonts
        self.workdir = workdir or tempfile.mkdtemp(prefix="pdftailor-")
        self.doc: PdfDoc | None = None
        self.blocks: dict[int, list[Block]] = {}
        self._lock = threading.RLock()
        self._serial = 0

    # ---- lifecycle ------------------------------------------------------------------
    @property
    def fonts(self) -> FontIndex:
        if self._fonts is None:
            self._fonts = default_index()
        return self._fonts

    def _tmp(self, stem: str, ext: str) -> str:
        self._serial += 1
        return os.path.join(self.workdir, f"{stem}-{self._serial}{ext}")

    def close(self) -> None:
        if self.doc:
            self.doc.close()
            self.doc = None
        self.blocks.clear()

    def dispose(self) -> None:
        self.close()
        shutil.rmtree(self.workdir, ignore_errors=True)

    def _need_doc(self) -> PdfDoc:
        if self.doc is None:
            raise EngineError("尚未開啟 PDF。")
        return self.doc

    # ---- open -----------------------------------------------------------------------
    def open(self, path: str) -> dict[str, Any]:
        with self._lock:
            self.close()
            self.doc = PdfDoc(path)
            pages = []
            for i, info in enumerate(self.doc.pages, start=1):
                _glyphs, stats = read_glyphs(self.doc, i)
                d = info.geometry.to_json()
                d["textLayer"] = stats.kind  # none / invisible / visible
                d["needsOcr"] = stats.kind != "visible"
                pages.append(d)
            return {"path": self.doc.path, "pageCount": self.doc.page_count, "pages": pages}

    def render_page(self, page: int, dpi: float = PREVIEW_DPI, path: str | None = None) -> dict[str, Any]:
        doc = self._need_doc()
        out = path or self._tmp(f"page{page}", ".png")
        w, h = doc.render_png(page, dpi / 72.0, out)
        return {"page": page, "path": out, "width": w, "height": h, "dpi": dpi}

    # ---- analysis -------------------------------------------------------------------
    def _ocr_engine(self, name: str, lines: list[dict] | None, scale: float) -> OcrEngine:
        if lines is not None:
            # Lines from the shell (Windows OCR) come in display points.
            return ExternalOcr(
                [
                    OcrLine(
                        str(l.get("text", "")),
                        float(l["x"]) * scale,
                        float(l["y"]) * scale,
                        float(l["w"]) * scale,
                        float(l["h"]) * scale,
                        float(l.get("confidence", -1)),
                    )
                    for l in lines
                ]
            )
        if name in ("auto", "tesseract"):
            if find_tesseract() is None:
                raise EngineError("找不到 Tesseract OCR。請把 tesseract 放在引擎旁的 tesseract 資料夾，或設定 PDFTAILOR_TESSERACT。")
            return TesseractOcr()
        raise EngineError(f"不認得的 OCR 引擎：{name}")

    def analyze(
        self,
        page: int,
        ocr: str = "auto",
        ocr_lines: list[dict] | None = None,
        force_ocr: bool = False,
        progress: Progress | None = None,
    ) -> dict[str, Any]:
        doc = self._need_doc()
        if not 1 <= page <= doc.page_count:
            raise EngineError(f"頁碼超出範圍：{page}")

        def step(stage: str, f: float) -> None:
            if progress is not None and progress(stage, f) is False:
                raise Cancelled()

        t0 = time.time()
        step("text-layer", 0.0)
        glyphs, stats = read_glyphs(doc, page)
        notes: list[str] = []
        if stats.kind == "visible" and not force_ocr:
            blocks = build_blocks(glyphs, page)
            mode = "text"
        else:
            scale = ANALYZE_DPI / 72.0
            step("render", 0.1)
            rgb = doc.render(page, scale)
            step("ocr", 0.2)
            try:
                engine = self._ocr_engine(ocr, ocr_lines, scale)
                res = analyze_scanned_page(rgb, scale, engine, page)
            except OcrUnavailable as ex:
                raise EngineError(str(ex)) from ex
            step("fonts", 0.85)
            blocks = res.blocks
            notes += res.notes
            self._refine_fonts(blocks, rgb, scale)
            mode = "ocr"
            if stats.kind == "invisible":
                notes.append("此頁已有隱藏的 OCR 文字層；已重新辨識，輸出時舊的隱藏文字會一併更新。")
        with self._lock:
            self.blocks[page] = blocks
        step("done", 1.0)
        return {
            "page": page,
            "mode": mode,
            "blocks": [b.to_json() for b in blocks],
            "notes": notes,
            "seconds": round(time.time() - t0, 2),
        }

    def _refine_fonts(self, blocks: list[Block], rgb: np.ndarray, scale: float) -> None:
        """Font family by shape matching, size from the matched font, snapped to Office sizes."""
        H, W = rgb.shape[:2]
        guesses: dict[int, Any] = {}
        for b in blocks:
            r = b.bounds.inflate(1.0, 1.0)
            x0, y0 = max(0, int(r.x * scale)), max(0, int(r.y * scale))
            x1, y1 = min(W, int(r.right * scale) + 1), min(H, int(r.bottom * scale) + 1)
            if x1 - x0 < 8 or y1 - y0 < 6:
                continue
            crop = np.ascontiguousarray(rgb[y0:y1, x0:x1])
            mask, _bg, _thr = ink_mask(crop, b.background)
            g = match_font(mask, b.text, b.style.bold, b.style.italic, self.fonts, scale)
            if g is not None and g.margin >= MIN_MARGIN:
                guesses[b.id] = g

        # Lines too short to judge take the family most lines of their script agreed on.
        votes: dict[str, Counter] = {"cjk": Counter(), "latin": Counter()}
        for b in blocks:
            g = guesses.get(b.id)
            if g:
                votes[_script(b.text)][g.family] += 1
        majority = {k: (v.most_common(1)[0][0] if v else None) for k, v in votes.items()}

        for b in blocks:
            g = guesses.get(b.id)
            style = b.style
            if g is not None:
                size = g.size if g.size and 0.7 <= g.size / style.size <= 1.4 else style.size
                style = style.with_(family=g.family, size=snap_size(size))
            else:
                fam = majority[_script(b.text)]
                style = style.with_(family=fam, size=snap_size(style.size))
            b.style = style
            for run in b.runs:
                run.style = style

    # ---- preview / export -----------------------------------------------------------
    def _edits(self, edits: list[dict]) -> list[Edit]:
        return [Edit.from_json(e) for e in edits]

    def preview(self, edits: list[dict], pages: list[int] | None = None, dpi: float = PREVIEW_DPI) -> dict[str, Any]:
        doc = self._need_doc()
        es = self._edits(edits)
        pdf_path = self._tmp("preview", ".pdf")
        report = write_edits(doc.path, es, pdf_path, fonts=self.fonts, doc=doc)
        want = pages or report.pages_changed or [1]
        out = PdfDoc(pdf_path)
        try:
            images = []
            for p in want:
                img = self._tmp(f"preview{p}", ".png")
                w, h = out.render_png(p, dpi / 72.0, img)
                images.append({"page": p, "path": img, "width": w, "height": h, "dpi": dpi})
        finally:
            out.close()
        return {"pdf": pdf_path, "images": images, "report": report.to_json()}

    def export(self, edits: list[dict], out_path: str, verify: bool = True) -> dict[str, Any]:
        doc = self._need_doc()
        out_path = os.path.abspath(out_path)
        if os.path.normcase(out_path) == os.path.normcase(doc.path):
            raise EngineError("不能覆蓋原始檔，請另存新檔。")
        es = self._edits(edits)
        report = write_edits(doc.path, es, out_path, fonts=self.fonts, doc=doc)
        result = {"report": report.to_json()}
        if verify:
            result["verify"] = verify_output(doc, out_path, es, report.pages_changed)
        return result

    # ---- Word -----------------------------------------------------------------------
    def export_docx(
        self,
        out_path: str,
        pages: list[int] | None = None,
        edits: list[dict] | None = None,
        ocr: str = "auto",
        progress: Progress | None = None,
    ) -> dict[str, Any]:
        """Writes the chosen pages as a flowing Word document (see :mod:`docstruct`)."""
        from .docstruct import PageInput
        from .tables import find_grids

        doc = self._need_doc()
        out_path = os.path.abspath(out_path)
        if os.path.normcase(out_path) == os.path.normcase(doc.path):
            raise EngineError("不能覆蓋原始檔，請另存新檔。")
        want = sorted(set(pages or range(1, doc.page_count + 1)))
        bad = [p for p in want if not 1 <= p <= doc.page_count]
        if bad:
            raise EngineError(f"頁碼超出範圍：{bad[0]}")
        by_page: dict[int, dict[int, Edit]] = {}
        for e in self._edits(edits or []):
            by_page.setdefault(e.page, {})[e.id] = e

        def step(stage: str, f: float) -> None:
            if progress is not None and progress(stage, f) is False:
                raise Cancelled()

        inputs: list[PageInput] = []
        n = len(want)
        for i, pno in enumerate(want):
            lo, span = i / n, 1.0 / n
            step(f"頁 {pno}", lo)
            if pno not in self.blocks:
                self.analyze(pno, ocr=ocr, progress=lambda st, f, lo=lo, span=span: (
                    progress is None or progress(f"頁 {pno}", lo + span * 0.7 * f) is not False))
            blocks = self._with_edits(self.blocks[pno], by_page.get(pno, {}))
            scale = WORD_DPI / 72.0
            rgb = doc.render(pno, scale)
            geo = doc.pages[pno - 1].geometry
            grids = find_grids(rgb, scale)
            inputs.append(PageInput(pno, geo.display_w, geo.display_h, blocks, grids, rgb, scale))
            step(f"頁 {pno}", lo + span * 0.9)
        step("寫入 Word", 0.95)
        from .docstruct import structure_document
        from .docxout import write_docx

        pages_doc = structure_document(inputs)
        stats = write_docx(pages_doc, out_path, title=os.path.splitext(os.path.basename(doc.path))[0])
        step("done", 1.0)
        return {"path": out_path, "pages": want, **stats}

    @staticmethod
    def _with_edits(blocks: list[Block], edits: dict[int, Edit]) -> list[Block]:
        if not edits:
            return list(blocks)
        out = []
        for b in blocks:
            e = edits.get(b.id)
            if e is None or not e.modified:
                out.append(b)
                continue
            if not e.text.strip():
                continue  # deleted in the editor
            runs = e.runs if e.runs and "".join(r.text for r in e.runs) == e.text else [Run(e.text, e.style, e.bounds.x, e.bounds.right)]
            out.append(replace(b, text=e.text, runs=runs, style=e.style, bounds=e.bounds))
        return out

    def fonts_list(self) -> dict[str, Any]:
        return {"families": self.fonts.families()}


def verify_output(src: PdfDoc, out_path: str, edits: list[Edit], pages: list[int], dpi: float = 72.0) -> dict[str, Any]:
    """Renders changed pages of both files and checks that nothing moved outside the
    edited lines. A pixel that changed far from every edit means the writer touched
    something it should not have."""
    out = PdfDoc(out_path)
    scale = dpi / 72.0
    problems = []
    try:
        if out.page_count != src.page_count:
            problems.append({"page": 0, "issue": "page-count"})
        for p in pages:
            a = src.render(p, scale).astype(np.int16)
            b = out.render(p, scale).astype(np.int16)
            if a.shape != b.shape:
                problems.append({"page": p, "issue": "page-size"})
                continue
            diff = np.abs(a - b).max(axis=2) > 24
            allowed = np.zeros(diff.shape, dtype=bool)
            for e in edits:
                if e.page != p or not e.modified:
                    continue
                grow = 0.6 * e.style.size
                for r in (e.original_bounds, e.bounds):
                    rr = r.inflate(grow + max(0.0, 0.0), grow)
                    # the new text may run on to the right of its box
                    rr = Rect(rr.x - grow, rr.y, rr.w + grow + max(0.0, len(e.text) * e.style.size - rr.w), rr.h)
                    y0, y1 = max(0, int(rr.y * scale)), int(rr.bottom * scale) + 1
                    x0, x1 = max(0, int(rr.x * scale)), int(rr.right * scale) + 1
                    allowed[y0:y1, x0:x1] = True
            stray = int((diff & ~allowed).sum())
            if stray > 0:
                problems.append({"page": p, "issue": "changed-outside-edits", "pixels": stray})
    finally:
        out.close()
    return {"ok": not problems, "problems": problems}
