import pikepdf
import pypdfium2 as pdfium
import pytest
from reportlab.pdfgen import canvas

from pdftailor_engine.contentstream import T, redact_stream, tokenize
from pdftailor_engine.redact import redact_page

WS = b"\x00\t\n\x0c\r "


def test_every_nonwhitespace_byte_is_covered():
    hostile = bytes([0x41, 0x28, 0x54, 0x6A, 0x20, 0x45, 0x49, 0x78, 0xFF, 0x00, 0x28, 0x28, 0x29] * 3)
    src = (
        b"q 1 0 0 1 72 720 cm BT /F1 12 Tf (Hello \\(world\\)) Tj 0 -14 Td [(A) -250 (B)] TJ ET Q\n"
        b"q BI /W 4 /H 1 /CS /G /BPC 8 /F /AHx ID " + hostile + b" EI Q\n"
        b"BT /F1 9 Tf 72 300 Td (After image) Tj ET % trailing comment"
    )
    toks = tokenize(src)
    covered = bytearray(len(src))
    for t in toks:
        for i in range(t.start, t.end):
            covered[i] = 1
    missed = [i for i, (c, b) in enumerate(zip(covered, src)) if not c and b not in WS]
    assert missed == []
    assert sum(1 for t in toks if t.kind is T.INLINE_IMAGE) == 1
    ops = [t.text for t in toks if t.kind is T.OPERATOR]
    assert ops[-3:] == ["Tf", "Td", "Tj"] or ops[-4:-1] == ["Tf", "Td", "Tj"]


def test_inline_image_length_hint_beats_embedded_ei():
    payload = b"xx EI yy"  # contains a fake EI
    src = b"BI /W 8 /H 1 /BPC 8 /CS /G /L %d ID " % len(payload) + payload + b"\nEI\nBT (after) Tj ET"
    toks = tokenize(src)
    assert [t.kind for t in toks].count(T.INLINE_IMAGE) == 1
    assert any(t.kind is T.OPERATOR and t.text == "Tj" for t in toks)


def test_redacts_only_the_target_line_and_keeps_vectors():
    src = (
        b"q 0.8 0.8 0.8 RG 2 w 100 400 m 500 400 l S 120 420 60 120 re f Q\n"
        b"BT /F1 12 Tf 72 700 Td (Line one keep me) Tj ET\n"
        b"BT /F1 12 Tf 72 680 Td (Line two redact me) Tj ET\n"
        b"BT /F1 12 Tf 1 0 0 1 72 660 Tm (Line three keep me) Tj 0 -20 Td (Line four keep me) Tj ET\n"
        b"BT /F1 10 Tf 300 300 Td [(Kerned) -200 (array) -200 (redact)] TJ ET\n"
    )
    res = redact_stream(src, [(70, 676, 320, 694), (298, 296, 430, 314)])
    out = res.content
    assert res.removed_runs == 2
    assert b"redact me" not in out and b"Kerned" not in out
    for keep in (b"Line one keep me", b"Line three keep me", b"Line four keep me"):
        assert keep in out
    assert b"100 400 m 500 400 l S" in out and b"120 420 60 120 re f" in out


def test_neighbouring_baselines_are_not_caught():
    """Lines at ordinary leading must not be swept up with the one being edited."""
    lines = b"".join(b"BT /F1 12 Tf 72 %d Td (line%d) Tj ET\n" % (700 - 14 * i, i) for i in range(5))
    # tight glyph box of line2: baseline 672, cap height ~9
    res = redact_stream(lines, [(72, 670.5, 120, 681.5)])
    assert res.removed_runs == 1
    assert b"(line2)" not in res.content and b"(line1)" in res.content and b"(line3)" in res.content


def test_ctm_and_graphics_state_are_tracked():
    src = (
        b"q 2 0 0 2 0 0 cm BT /F1 6 Tf 36 100 Td (Scaled) Tj ET Q\n"
        b"q 1 0 0 1 100 0 cm BT /F1 12 Tf 0 500 Td (Shifted) Tj ET Q\n"
        b"BT /F1 12 Tf 72 300 Td (AfterRestore) Tj ET\n"
    )
    assert b"(Scaled)" not in redact_stream(src, [(70, 195, 200, 215)]).content  # (72, 200)
    assert b"(Shifted)" not in redact_stream(src, [(98, 496, 200, 514)]).content  # (100, 500)
    out = redact_stream(src, [(70, 296, 200, 314)]).content  # (72, 300)
    assert b"(AfterRestore)" not in out and b"(Scaled)" in out and b"(Shifted)" in out


def test_hex_strings_become_empty_hex():
    src = b"BT /F1 12 Tf 72 700 Td <00480065006C006C006F> Tj ET"
    assert b"<>" in redact_stream(src, [(70, 698, 200, 712)]).content


# ---- page level, including forms ------------------------------------------------------
def _page_with_form(path, shared=False):
    c = canvas.Canvas(path, pagesize=(612, 792))
    c.beginForm("hdr")
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "Inside the form")
    c.drawString(72, 680, "Keep inside form")
    c.endForm()
    c.doForm("hdr")
    c.setFont("Helvetica", 12)
    c.drawString(72, 600, "Plain page text")
    c.showPage()
    if shared:
        c.doForm("hdr")
        c.showPage()
    c.save()
    return path


def _text(path, page=0):
    doc = pdfium.PdfDocument(path)
    tp = doc[page].get_textpage()
    return tp.get_text_range()


def test_text_inside_form_xobject_is_removed(tmp_path):
    src = _page_with_form(str(tmp_path / "f.pdf"))
    out = str(tmp_path / "o.pdf")
    with pikepdf.open(src) as pdf:
        removed = redact_page(pdf, pdf.pages[0], [(70, 697, 200, 712)])
        pdf.save(out)
    assert removed == 1
    text = _text(out)
    assert "Inside the form" not in text
    assert "Keep inside form" in text and "Plain page text" in text


def test_shared_form_is_not_modified_for_other_pages(tmp_path):
    src = _page_with_form(str(tmp_path / "f.pdf"), shared=True)
    out = str(tmp_path / "o.pdf")
    with pikepdf.open(src) as pdf:
        assert len(pdf.pages) == 2
        redact_page(pdf, pdf.pages[0], [(70, 697, 200, 712)])
        pdf.save(out)
    assert "Inside the form" not in _text(out, 0)
    assert "Inside the form" in _text(out, 1), "page 2 lost text it shares through the form"
