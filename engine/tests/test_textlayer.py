"""Text-layer reading: alignment against rendered pixels, styles, invisible layers."""

import numpy as np
import pytest
from fixtures import TextItem, ink_bbox, make_text_pdf

from pdftailor_engine.pdfio import PdfDoc
from pdftailor_engine.textlayer import build_blocks, read_glyphs

SCALE = 4.0


def _blocks(path, page=1):
    doc = PdfDoc(path)
    glyphs, stats = read_glyphs(doc, page)
    return doc, build_blocks(glyphs, page), stats


# display space: x from the left, y = baseline measured down from the top
ITEMS = [
    TextItem(72, 92, "Quarterly Report", "Helvetica-Bold", 20),
    TextItem(72, 132, "Revenue grew steadily over the year.", "Helvetica", 12),
    TextItem(72, 172, "Margins stayed flat.", "Times-Italic", 14, (200, 30, 30)),
]


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
@pytest.mark.parametrize("crop", [None, (30, 40, 580, 760)])
def test_block_boxes_land_on_rendered_ink(tmp_path, rotate, crop):
    """The display box of every line must coincide with the ink pdfium actually draws."""
    path = make_text_pdf(str(tmp_path / "a.pdf"), ITEMS, rotate=rotate, crop=crop)
    doc, blocks, _ = _blocks(path)
    geom = doc.geometry(1)
    assert not doc.pages[0].size_mismatch, "geometry disagrees with pdfium's page size"

    rgb = doc.render(1, SCALE)
    assert len(blocks) == 3, [b.text for b in blocks]
    for b in blocks:
        r = b.bounds
        region = (
            int((r.x - 3) * SCALE),
            int((r.y - 3) * SCALE),
            int((r.right + 3) * SCALE),
            int((r.bottom + 3) * SCALE),
        )
        ink = ink_bbox(rgb, region)
        assert ink is not None, f"no ink under block {b.text!r} at {r}"
        ix0, iy0, ix1, iy1 = (v / SCALE for v in ink)
        # Tight glyph boxes should hug the ink to within ~1.5 pt.
        assert abs(ix0 - r.x) < 1.5, (b.text, ix0, r.x)
        assert abs(ix1 - r.right) < 1.5, (b.text, ix1, r.right)
        assert abs(iy0 - r.y) < 2.5, (b.text, iy0, r.y)
        assert abs(iy1 - r.bottom) < 2.5, (b.text, iy1, r.bottom)


def test_text_is_read_back_in_order(tmp_path):
    path = make_text_pdf(str(tmp_path / "a.pdf"), ITEMS)
    _, blocks, _ = _blocks(path)
    assert [b.text for b in blocks] == [
        "Quarterly Report",
        "Revenue grew steadily over the year.",
        "Margins stayed flat.",
    ]


def test_styles_are_read_from_the_fonts(tmp_path):
    path = make_text_pdf(str(tmp_path / "a.pdf"), ITEMS)
    _, blocks, _ = _blocks(path)
    title, body, italic = (b.style for b in blocks)

    assert title.bold and not title.italic
    assert abs(title.size - 20) < 0.6
    assert not body.bold and not body.italic
    assert abs(body.size - 12) < 0.6
    assert italic.italic and not italic.bold
    assert italic.color == (200, 30, 30)
    assert all(b.style.source == "font" for b in blocks)


def test_wide_gap_splits_table_cells(tmp_path):
    items = [
        TextItem(72, 92, "Item", "Helvetica", 12),
        TextItem(300, 92, "Price", "Helvetica", 12),
        TextItem(450, 92, "Qty", "Helvetica", 12),
    ]
    path = make_text_pdf(str(tmp_path / "a.pdf"), items)
    _, blocks, _ = _blocks(path)
    assert [b.text for b in blocks] == ["Item", "Price", "Qty"]


def test_mixed_style_line_keeps_runs(tmp_path):
    items = [
        TextItem(72, 92, "Total: ", "Helvetica", 12),
        TextItem(72 + 34, 92, "42 units", "Helvetica-Bold", 12),
    ]
    path = make_text_pdf(str(tmp_path / "a.pdf"), items)
    _, blocks, _ = _blocks(path)
    assert len(blocks) == 1
    b = blocks[0]
    assert b.text == "Total: 42 units"
    assert "".join(r.text for r in b.runs) == b.text
    assert [r.style.bold for r in b.runs] == [False, True]


def test_invisible_ocr_layer_is_detected(tmp_path):
    """A scan with a hidden OCR text layer must not be mistaken for editable text."""
    items = [TextItem(72, 92, "Hidden words", "Helvetica", 12, mode=3)]
    path = make_text_pdf(str(tmp_path / "a.pdf"), items)
    _, blocks, stats = _blocks(path)
    assert stats.kind == "invisible"
    assert blocks == []


def test_blank_page_has_no_text_layer(tmp_path):
    path = make_text_pdf(str(tmp_path / "a.pdf"), [])
    _, blocks, stats = _blocks(path)
    assert stats.kind == "none" and blocks == []


def test_size_matches_pdfium_for_inherited_boxes(tmp_path):
    """pikepdf must see boxes inherited from the page tree the way pdfium does."""
    import pikepdf

    path = make_text_pdf(str(tmp_path / "a.pdf"), ITEMS)
    out = str(tmp_path / "inh.pdf")
    with pikepdf.open(path) as pdf:
        page = pdf.pages[0].obj
        pages_node = pdf.Root.Pages
        pages_node.MediaBox = page.MediaBox
        del page["/MediaBox"]
        pdf.save(out)
    doc = PdfDoc(out)
    assert not doc.pages[0].size_mismatch
    assert doc.pages[0].geometry.display_w == 612


@pytest.mark.parametrize("rotate", [90, 180, 270])
def test_sideways_text_on_rotated_page_is_skipped_not_misplaced(tmp_path, rotate):
    """Text horizontal in user space shows sideways after /Rotate; it cannot be edited
    as a normal line, so it must be left out rather than reported at a wrong place."""
    items = [TextItem(72, 600, "sideways", "Helvetica", 12)]
    path = make_text_pdf(str(tmp_path / "a.pdf"), items, rotate=rotate, user_space_text=True)
    _, blocks, stats = _blocks(path)
    assert blocks == []
    assert stats.skipped_rotated == len("sideways")
