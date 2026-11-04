"""End-to-end: a synthetic scan goes through the OCR pipeline and comes out with the right geometry/styles."""
import pytest

from scangen import Style
from scanpdf import PX_PER_PT, build_scan, write_scan_pdf
from pdftailor_engine.ocr.tesseract import TesseractOcr, find_tesseract
from pdftailor_engine.pdfio import PdfDoc
from pdftailor_engine.scanpage import analyze_scanned_page

pytestmark = pytest.mark.skipif(find_tesseract() is None, reason="tesseract not installed")


def _body(**k):
    return Style(size_px=int(11 * PX_PER_PT), **k)


ITEMS = [
    (60, 60, "季度營運報告", Style(weight="Bold", size_px=int(20 * PX_PER_PT))),
    (60, 105, "本公司所有資料均依個資法處理，請放心。", _body()),
    (60, 165, "■ 項目一　□ 項目二　● 項目三", _body()),
    (60, 190, "「引用文字」與『書名』", _body()),
    (60, 250, "Quarterly report revenue grew steadily", Style(family="dejavu", size_px=int(11 * PX_PER_PT), shear=0.21)),
    (60, 275, "Total amount due within thirty days", Style(family="dejavu", size_px=int(11 * PX_PER_PT))),
    (60, 320, "品名", Style(weight="Bold", size_px=int(11 * PX_PER_PT))),
    (250, 320, "數量", Style(weight="Bold", size_px=int(11 * PX_PER_PT))),
    (420, 320, "單價", Style(weight="Bold", size_px=int(11 * PX_PER_PT))),
    (60, 345, "六角螺絲", _body()),
]


@pytest.fixture(scope="module")
def blocks(tmp_path_factory):
    page, _truth = build_scan(ITEMS)
    path = write_scan_pdf(str(tmp_path_factory.mktemp("scan") / "scan.pdf"), page)
    doc = PdfDoc(path)
    scale = 240 / 72
    res = analyze_scanned_page(doc.render(1, scale), scale, TesseractOcr(), 1)
    return res.blocks


def _find(blocks, needle):
    hits = [b for b in blocks if needle in b.text]
    assert hits, f"no block containing {needle!r}: {[b.text for b in blocks]}"
    return hits[0]


def test_title_is_bold_and_larger(blocks):
    title = _find(blocks, "季度")
    body = _find(blocks, "請放心") if any("請放心" in b.text for b in blocks) else _find(blocks, "本公司")
    assert title.style.bold and not body.style.bold
    assert title.style.size > body.style.size * 1.5


def test_body_size_close_to_11pt(blocks):
    body = _find(blocks, "本公司")
    assert 10.0 <= body.style.size <= 12.0


def test_symbols_recognised_in_order(blocks):
    line = _find(blocks, "項目")
    assert [c for c in line.text if c in "■□●"] == ["■", "□", "●"]
    quote = _find(blocks, "引用")
    assert quote.text.replace(" ", "") .startswith("「") and "」" in quote.text and "『" in quote.text
    assert " " not in quote.text  # no spaces invented next to brackets


def test_italic_only_on_the_slanted_line(blocks):
    assert _find(blocks, "Quarterly").style.italic
    assert not _find(blocks, "Total").style.italic


def test_short_bold_headers(blocks):
    for word in ("品名", "數量", "單價"):
        assert _find(blocks, word).style.bold, word
    assert not _find(blocks, "六角").style.bold
