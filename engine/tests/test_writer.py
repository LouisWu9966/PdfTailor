"""Writer: text lands where the old text was, nothing else on the page changes."""
import numpy as np
import pikepdf
import pytest

from fixtures import TextItem, ink_bbox, make_text_pdf
from pdftailor_engine.fonts import FontIndex
from pdftailor_engine.models import Edit
from pdftailor_engine.pdfio import PdfDoc
from pdftailor_engine.service import Session, verify_output
from pdftailor_engine.textlayer import build_blocks, read_glyphs
from pdftailor_engine.writer import write_edits

SCALE = 3.0


@pytest.fixture(scope="module")
def fonts(tmp_path_factory):
    return FontIndex.scan(cache_path=str(tmp_path_factory.mktemp("fi") / "fi.json"))


def _page_text(path, page=1):
    doc = PdfDoc(path)
    try:
        glyphs, _ = read_glyphs(doc, page)
        return "".join(b.text for b in build_blocks(glyphs, page))
    finally:
        doc.close()


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_text_layer_edit_rotated(tmp_path, fonts, rotate):
    src = make_text_pdf(
        str(tmp_path / f"src{rotate}.pdf"),
        [TextItem(72, 100, "Invoice total 1200", size=14), TextItem(72, 160, "Keep this line", size=12)],
        rotate=rotate,
    )
    doc = PdfDoc(src)
    glyphs, _ = read_glyphs(doc, 1)
    blocks = build_blocks(glyphs, 1)
    target = next(b for b in blocks if b.text.startswith("Invoice"))
    edit = Edit.from_block(target, text="Invoice total 3400")
    out = str(tmp_path / f"out{rotate}.pdf")
    rep = write_edits(src, [edit] + [Edit.from_block(b) for b in blocks if b is not target], out, fonts=fonts, doc=doc)
    assert rep.runs_removed >= 1

    text = _page_text(out)
    assert "1200" not in text and "3400" in text and "Keep this line" in text

    a, b = doc.render(1, SCALE), PdfDoc(out).render(1, SCALE)
    r = target.bounds.inflate(4, 4)
    region = (int(r.x * SCALE), int(r.y * SCALE), int((r.right + 60) * SCALE), int(r.bottom * SCALE))
    old, new = ink_bbox(a, region), ink_bbox(b, region)
    assert abs(old[0] - new[0]) <= 1.5 * SCALE and abs(old[1] - new[1]) <= 1.5 * SCALE and abs(old[3] - new[3]) <= 1.5 * SCALE
    assert verify_output(doc, out, [edit], [1])["ok"]


def test_untouched_pages_and_streams_are_byte_identical(tmp_path, fonts):
    from reportlab.pdfgen import canvas

    src = str(tmp_path / "two.pdf")
    c = canvas.Canvas(src)
    c.drawString(72, 700, "Page one text")
    c.showPage()
    c.drawString(72, 700, "Page two text")
    c.showPage()
    c.save()
    doc = PdfDoc(src)
    glyphs, _ = read_glyphs(doc, 1)
    blk = build_blocks(glyphs, 1)[0]
    out = str(tmp_path / "two_out.pdf")
    write_edits(src, [Edit.from_block(blk, text="Page one changed")], out, fonts=fonts, doc=doc)
    with pikepdf.open(src) as p1, pikepdf.open(out) as p2:
        assert p1.pages[1].obj.Contents.read_raw_bytes() == p2.pages[1].obj.Contents.read_raw_bytes()
    assert "Page two text" in _page_text(out, 2)


def test_scan_edit_keeps_image_and_leaves_no_ghost(tmp_path, fonts):
    from scangen import Style
    from scanpdf import PX_PER_PT, build_scan, write_scan_pdf
    from pdftailor_engine.ocr.tesseract import find_tesseract

    if find_tesseract() is None:
        pytest.skip("tesseract not installed")
    items = [
        (60, 100, "Total amount due within thirty days", Style(family="dejavu", size_px=int(11 * PX_PER_PT))),
        (60, 140, "請於收到通知後七個工作天內回覆。", Style(size_px=int(11 * PX_PER_PT))),
    ]
    page, _ = build_scan(items, chart=False)
    src = write_scan_pdf(str(tmp_path / "scan.pdf"), page)
    s = Session(fonts=fonts, workdir=str(tmp_path / "work"))
    s.open(src)
    res = s.analyze(1)
    edits = []
    for b in res["blocks"]:
        e = dict(b, originalText=b["text"], originalBounds=b["bounds"])
        if "Total" in b["text"]:
            e["text"] = "Total amount due now"  # much shorter: the old tail must vanish
            tail = b
        edits.append(e)
    out = str(tmp_path / "scan_out.pdf")
    r = s.export(edits, out)
    assert r["verify"]["ok"], r["verify"]

    with pikepdf.open(src) as p1, pikepdf.open(out) as p2:
        raw1 = {bytes(v.read_raw_bytes()) for _, v in p1.pages[0].images.items()}
        raw2 = {bytes(v.read_raw_bytes()) for _, v in p2.pages[0].images.items()}
        assert raw1 <= raw2  # the scan image is untouched, byte for byte

    # where the old "thirty days" was, past the end of the new text, there is only paper
    rgb = PdfDoc(out).render(1, SCALE)
    x, y, w, h = tail["bounds"]
    line = (int(x * SCALE), int(y * SCALE), int((x + w + 20) * SCALE), int((y + h) * SCALE))
    new_right = ink_bbox(rgb, line, thresh=120)[2]
    reg = rgb[line[1] : line[3], new_right + int(2 * SCALE) : int((x + w + 2) * SCALE)].astype(int)
    assert reg.shape[1] > 20 * SCALE  # the erased tail is really there to check
    paper = np.array([250, 250, 248])
    assert np.abs(reg - paper).sum(axis=2).max() < 60
    s.dispose()
