"""OCR engine interface.

Engines differ a lot in quality on Chinese, and the best one varies by document, so the
analysis code depends only on this interface. Two things are asked of an engine:

* find lines on a page and read them (``recognize_page``);
* read one cropped line again (``recognize_crop``) — optional, but it is what lets the
  analyser cut a line apart at ■ □ ● and read the pieces separately, instead of letting
  the symbol corrupt the text beside it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OcrLine:
    text: str
    x: float  # pixels of the image that was recognised
    y: float
    w: float
    h: float
    confidence: float  # 0..100, mean over words; -1 when unknown


class OcrUnavailable(Exception):
    """The engine cannot run here (binary or language data missing)."""


class OcrEngine:
    name = "base"
    can_recrop = False
    dpi: float = 300.0  # resolution of the images passed in; the analyser sets it

    def recognize_page(self, rgb) -> list[OcrLine]:  # numpy HxWx3 uint8
        raise NotImplementedError

    def recognize_crop(self, rgb) -> tuple[str, float]:
        raise NotImplementedError


class ExternalOcr(OcrEngine):
    """Lines supplied by the caller — e.g. Windows OCR run by the desktop shell."""

    name = "external"
    can_recrop = False

    def __init__(self, lines: list[OcrLine]):
        self._lines = lines

    def recognize_page(self, rgb) -> list[OcrLine]:
        return list(self._lines)
