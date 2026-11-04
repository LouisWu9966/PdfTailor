"""Opening, measuring and rendering PDFs.

Rendering goes through pdfium (``pypdfium2``, Apache-2.0/BSD) and page geometry through
pikepdf, which — unlike pdfium's box getters — resolves boxes and ``/Rotate`` that are
inherited from the page tree. The two are cross-checked on open: if pdfium renders a
page at a different size than the geometry predicts, the mismatch is reported instead of
silently producing misplaced overlays.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
import pikepdf
import pypdfium2 as pdfium

from .geometry import PageGeometry


class PdfOpenError(Exception):
    """The file could not be opened (missing, corrupt, or password protected)."""


def _inherited(obj, key: str):
    """Looks a page attribute up through the /Parent chain, as the PDF spec requires."""
    seen = 0
    while obj is not None and seen < 64:
        if key in obj:
            return obj[key]
        obj = obj.get("/Parent")
        seen += 1
    return None


@dataclass
class PageInfo:
    geometry: PageGeometry
    pdfium_size: tuple[float, float]

    @property
    def size_mismatch(self) -> bool:
        return (
            abs(self.pdfium_size[0] - self.geometry.display_w) > 1.0
            or abs(self.pdfium_size[1] - self.geometry.display_h) > 1.0
        )


class PdfDoc:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        if not os.path.isfile(self.path):
            raise PdfOpenError(f"找不到檔案：{path}")
        try:
            self.pdfium = pdfium.PdfDocument(self.path)
        except Exception as ex:  # pdfium raises PdfiumError with a status code
            msg = str(ex)
            if "assword" in msg:
                raise PdfOpenError("這份 PDF 有密碼保護，目前無法編輯。") from ex
            raise PdfOpenError(f"無法開啟 PDF：{msg}") from ex

        self.pages: list[PageInfo] = []
        try:
            with pikepdf.open(self.path) as pk:
                for i, page in enumerate(pk.pages):
                    obj = page.obj
                    media = _inherited(obj, "/MediaBox")
                    crop = _inherited(obj, "/CropBox")
                    rot = _inherited(obj, "/Rotate")
                    media_t = tuple(float(v) for v in media) if media is not None else (0.0, 0.0, 612.0, 792.0)
                    crop_t = tuple(float(v) for v in crop) if crop is not None else None
                    geom = PageGeometry(i + 1, media_t, crop_t, int(rot) if rot is not None else 0)
                    size = self.pdfium[i].get_size()
                    self.pages.append(PageInfo(geom, (float(size[0]), float(size[1]))))
        except pikepdf.PasswordError as ex:
            raise PdfOpenError("這份 PDF 有密碼保護，目前無法編輯。") from ex
        except pikepdf.PdfError as ex:
            raise PdfOpenError(f"PDF 結構損壞：{ex}") from ex

    # ---- basics ------------------------------------------------------------------
    @property
    def page_count(self) -> int:
        return len(self.pages)

    def geometry(self, page_number: int) -> PageGeometry:
        return self.pages[page_number - 1].geometry

    def close(self) -> None:
        try:
            self.pdfium.close()
        except Exception:
            pass

    # ---- rendering ---------------------------------------------------------------
    def render(self, page_number: int, scale: float) -> np.ndarray:
        """Renders a page to an RGB uint8 array. ``scale`` is pixels per point."""
        page = self.pdfium[page_number - 1]
        bitmap = page.render(scale=scale, rotation=0, rev_byteorder=True)
        arr = bitmap.to_numpy()
        if arr.ndim == 3 and arr.shape[2] == 4:
            arr = arr[:, :, :3]
        return np.ascontiguousarray(arr)

    def render_png(self, page_number: int, scale: float, out_path: str) -> tuple[int, int]:
        from PIL import Image

        arr = self.render(page_number, scale)
        Image.fromarray(arr).save(out_path, format="PNG", optimize=False)
        return arr.shape[1], arr.shape[0]
