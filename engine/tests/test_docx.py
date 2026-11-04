"""Word export: paragraphs, headings, lists and tables come out as real Word structure."""
import os

import pytest
from docx import Document
from docx.oxml.ns import qn

import docfix
from pdftailor_engine.service import EngineError, Session

pytestmark = pytest.mark.skipif(not docfix.HAVE_FONTS, reason="test fonts missing")


def export(tmp_path, pdf, **kw):
    s = Session()
    s.open(pdf)
    out = str(tmp_path / "out.docx")
    res = s.export_docx(out, **kw)
    return Document(out), res, s


def body(doc):
    """('p', paragraph) / ('t', table) in document order."""
    import docx.table as T
    import docx.text.paragraph as P

    items = []
    for el in doc.element.body:
        if el.tag == qn("w:p"):
            items.append(("p", P.Paragraph(el, doc)))
        elif el.tag == qn("w:tbl"):
            items.append(("t", T.Table(el, doc)))
    return items


def paras(doc):
    return [p for k, p in body(doc) if k == "p" and p.text.strip()]


def test_lines_merge_into_paragraphs(tmp_path):
    doc, res, _ = export(tmp_path, docfix.report_pdf(str(tmp_path / "r.pdf")))
    texts = [p.text for p in paras(doc)]
    summary = next(t for t in texts if t.startswith("The third quarter"))
    assert summary.endswith("previous quarter.")
    assert "\n" not in summary and "  " not in summary
    assert "yield across all production lines, driven mainly" in summary
    second = next(t for t in texts if t.startswith("Several issues"))
    assert second.endswith("during the review.")
    # the two paragraphs stay two paragraphs
    assert summary != second


def test_first_line_indent_and_spacing(tmp_path):
    doc, _, _ = export(tmp_path, docfix.report_pdf(str(tmp_path / "r.pdf")))
    second = next(p for p in paras(doc) if p.text.startswith("Several issues"))
    assert second.paragraph_format.left_indent in (None, 0) or second.paragraph_format.left_indent.pt < 1
    assert 15 < second.paragraph_format.first_line_indent.pt < 25


def test_headings_and_levels(tmp_path):
    doc, res, _ = export(tmp_path, docfix.report_pdf(str(tmp_path / "r.pdf")))
    heads = {p.text: p.style.name for p in paras(doc) if p.style.name.startswith("Heading")}
    assert heads["Quarterly Quality Report"] == "Heading 1"
    assert heads["1. Summary"] == "Heading 2"
    assert heads["2. Actions"] == "Heading 2"
    assert heads["3. Results"] == "Heading 2"
    assert res["headings"] == 4


def test_title_centred_and_right_tab(tmp_path):
    doc, _, _ = export(tmp_path, docfix.report_pdf(str(tmp_path / "r.pdf")))
    ps = paras(doc)
    assert ps[0].alignment == 1  # CENTER
    line = next(p for p in ps if p.text.startswith("Prepared by"))
    assert line.text == "Prepared by: QA Department\t2026-10-31"
    tabs = list(line.paragraph_format.tab_stops)
    assert len(tabs) == 1 and tabs[0].alignment == 2  # RIGHT


def test_bullets_become_hanging_list(tmp_path):
    doc, res, _ = export(tmp_path, docfix.report_pdf(str(tmp_path / "r.pdf")))
    items = [p for p in paras(doc) if p.text.startswith("•")]
    assert [p.text.split("\t", 1)[1][:12] for p in items] == ["Complete the", "Re-calibrate", "Review the s"]
    assert items[0].text.endswith("quality board.")  # wrapped bullet is one paragraph
    for p in items:
        assert p.paragraph_format.first_line_indent.pt < -5
        assert p.paragraph_format.left_indent.pt > 5
    assert res["lists"] == 3


def test_numbered_list(tmp_path):
    doc, res, _ = export(tmp_path, docfix.numbered_pdf(str(tmp_path / "n.pdf")))
    items = [p for p in paras(doc) if p.text[:2] in ("1.", "2.", "3.")]
    assert len(items) == 3
    assert items[0].text.startswith("1.\tOpen the valve")
    assert items[0].text.endswith("for at least a minute.")


def test_borderless_table(tmp_path):
    doc, res, _ = export(tmp_path, docfix.report_pdf(str(tmp_path / "r.pdf")))
    tables = doc.tables
    assert len(tables) == 1
    rows = [[c.text for c in r.cells] for r in tables[0].rows]
    assert rows == [["Line", "Yield %", "Defects", "Notes"], ["A1", "98.2", "12", "Stable"],
                    ["B2", "95.7", "31", "Tooling issue under review"], ["C3", "97.4", "18", "Improved"]]
    # numeric columns keep their right alignment
    assert tables[0].rows[1].cells[1].paragraphs[0].alignment == 2


def test_text_before_and_after_table_stay_outside(tmp_path):
    doc, _, _ = export(tmp_path, docfix.report_pdf(str(tmp_path / "r.pdf")))
    items = body(doc)
    kinds = [k for k, _ in items]
    ti = kinds.index("t")
    assert items[ti - 1][1].text == "3. Results"
    assert items[ti + 1][1].text.startswith("Figures are preliminary")


def test_ruled_table_structure(tmp_path):
    doc, res, _ = export(tmp_path, docfix.ruled_pdf(str(tmp_path / "t.pdf")))
    assert res["tables"] == 1
    t = doc.tables[0]
    assert len(t.rows) == 5 and len(t.columns) == 4
    tcs = [[tc for tc in tr._tr.tc_lst] for tr in t.rows]
    # row 4: "Approved by..." spans two columns -> gridSpan=2
    spans = [tc.grid_span for tc in tcs[4]]
    assert 2 in spans
    # column 0 rows 2-3 merged vertically
    vm = [tc.tcPr.find(qn("w:vMerge")) is not None for tc in (tcs[2][0], tcs[3][0])]
    assert vm[1] is True
    text = lambda r, c: t.cell(r, c).text
    assert text(0, 0) == "Item" and text(0, 3) == "Result"
    assert text(1, 1) == "No scratches, dents or discolouration visible"  # wrapped cell text -> one paragraph
    assert text(1, 3) == "12.50"
    assert text(2, 0) == "Size" and text(2, 2) == "±0.1 mm"
    assert text(4, 1) == "Approved by the quality manager"


def test_ruled_table_borders_and_shading(tmp_path):
    doc, _, _ = export(tmp_path, docfix.ruled_pdf(str(tmp_path / "t.pdf")))
    t = doc.tables[0]
    tc = t.cell(0, 0)._tc
    shd = tc.tcPr.find(qn("w:shd"))
    assert shd is not None and shd.get(qn("w:fill")) not in (None, "FFFFFF")
    b = tc.tcPr.find(qn("w:tcBorders"))
    assert b.find(qn("w:top")).get(qn("w:val")) == "single"
    # a body cell has no fill
    assert t.cell(1, 1)._tc.tcPr.find(qn("w:shd")) is None
    # right-aligned numbers
    assert t.cell(1, 3).paragraphs[0].alignment == 2


def test_text_after_table_is_a_paragraph(tmp_path):
    doc, _, _ = export(tmp_path, docfix.ruled_pdf(str(tmp_path / "t.pdf")))
    last = [p for p in paras(doc)][-1]
    assert last.text.startswith("Sign the record after the final check")
    assert last.text.endswith("at least three years.")


def test_cjk_paragraphs_headings_and_lists(tmp_path):
    doc, res, _ = export(tmp_path, docfix.cjk_pdf(str(tmp_path / "c.pdf")))
    ps = paras(doc)
    assert ps[0].text == "品質管理程序書" and ps[0].alignment == 1
    d = {p.text: p for p in ps}
    assert d["一、目的"].style.name.startswith("Heading")
    purpose = next(p for p in ps if p.text.startswith("本程序規範"))
    assert " " not in purpose.text and "\n" not in purpose.text
    assert purpose.text.endswith("執行時之依據。")
    assert purpose.paragraph_format.first_line_indent.pt > 15
    second = next(p for p in ps if p.text.startswith("各部門應依"))
    assert second.text.endswith("核准。")
    items = [p for p in ps if p.text.startswith(("（一）", "（二）"))]
    assert len(items) == 2 and items[0].text.startswith("（一）\t品保部")


def test_page_range_and_sections(tmp_path):
    pdf = docfix.ruled_pdf(str(tmp_path / "t3.pdf"), pages=3)
    doc, res, _ = export(tmp_path, pdf, pages=[2, 3])
    assert res["pages"] == [2, 3]
    assert len(doc.sections) == 2
    heads = [p.text for p in paras(doc) if p.style.name.startswith("Heading")]
    assert heads == ["Inspection plan 2", "Inspection plan 3"]
    assert len(doc.tables) == 2


def test_page_range_errors(tmp_path):
    s = Session()
    s.open(docfix.ruled_pdf(str(tmp_path / "t.pdf")))
    with pytest.raises(EngineError):
        s.export_docx(str(tmp_path / "o.docx"), pages=[5])
    with pytest.raises(EngineError):
        s.export_docx(s.doc.path)  # never overwrite the source


def test_edits_override_text(tmp_path):
    pdf = docfix.report_pdf(str(tmp_path / "r.pdf"))
    s = Session()
    s.open(pdf)
    blocks = s.analyze(1)["blocks"]
    b = next(x for x in blocks if x["text"].endswith("Re-calibrate the two instruments."))
    edit = dict(b, originalText=b["text"], originalBounds=b["bounds"], text="\u2022 Re-calibrate all instruments.")
    out = str(tmp_path / "o.docx")
    s.export_docx(out, edits=[edit])
    texts = [p.text for p in paras(Document(out))]
    assert any(t.endswith("Re-calibrate all instruments.") for t in texts)
    assert not any("two instruments." in t and t.startswith("•") for t in texts)


def test_run_formatting_kept(tmp_path):
    doc, _, _ = export(tmp_path, docfix.report_pdf(str(tmp_path / "r.pdf")))
    summary = next(p for p in paras(doc) if p.text.startswith("The third quarter"))
    assert summary.runs[0].font.size.pt == 10.5
    head = next(p for p in paras(doc) if p.text == "1. Summary")
    assert head.runs[0].font.bold


def test_progress_and_cancel(tmp_path):
    from pdftailor_engine.service import Cancelled

    s = Session()
    s.open(docfix.ruled_pdf(str(tmp_path / "t.pdf"), pages=3))
    seen = []
    s.export_docx(str(tmp_path / "a.docx"), progress=lambda st, f: seen.append((st, f)) or True)
    assert seen[-1][1] == 1.0 and len(seen) >= 4
    with pytest.raises(Cancelled):
        s.export_docx(str(tmp_path / "b.docx"), progress=lambda st, f: False)
    assert not os.path.exists(tmp_path / "b.docx")


def _scan_of(src, dst, dpi=200):
    import numpy as np
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    from pdftailor_engine.pdfio import PdfDoc

    d = PdfDoc(src)
    base = d.render(1, dpi / 72).astype(np.int16)
    d.close()
    noisy = np.clip(base + np.random.default_rng(1).integers(-6, 7, base.shape), 0, 255).astype(np.uint8)
    c = canvas.Canvas(dst, pagesize=(595, 842))
    c.drawImage(ImageReader(Image.fromarray(noisy)), 0, 0, 595, 842)
    c.showPage()
    c.save()
    return dst


@pytest.mark.skipif(__import__("shutil").which("tesseract") is None and not os.environ.get("PDFTAILOR_TESSERACT"),
                    reason="Tesseract not installed")
def test_scanned_table_page(tmp_path):
    src = docfix.ruled_pdf(str(tmp_path / "t.pdf"))
    scan = _scan_of(src, str(tmp_path / "scan.pdf"))
    doc, res, _ = export(tmp_path, scan)
    assert res["tables"] == 1
    t = doc.tables[0]
    assert len(t.rows) == 5 and len(t.columns) == 4
    assert t.cell(0, 0).text == "Item" and t.cell(0, 3).text == "Result"
    assert "|" not in "".join(c.text for r in t.rows for c in r.cells)  # rule marks are not text
    assert t.cell(1, 3).text == "12.50" and t.cell(3, 3).text == "49.98"
    tcs = [list(r._tr.tc_lst) for r in t.rows]
    assert 2 in [tc.grid_span for tc in tcs[4]]
    assert t.cell(1, 0)._tc.tcPr.find(qn("w:shd")) is None
    assert t.cell(0, 0)._tc.tcPr.find(qn("w:shd")) is not None


@pytest.mark.parametrize("dark", [True, False])
def test_shaded_header_band_belongs_to_table(tmp_path, dark):
    doc, res, _ = export(tmp_path, docfix.band_pdf(str(tmp_path / "b.pdf"), dark))
    assert res["tables"] == 1 and not paras(doc)
    t = doc.tables[0]
    assert [c.text for c in t.rows[0].cells] == ["Item", "Check", "Limit"]
    assert len(t.rows) == 4
    assert t.cell(0, 0)._tc.tcPr.find(qn("w:shd")) is not None  # the band's colour survives
