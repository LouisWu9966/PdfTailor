"""Writes the structured pages (:mod:`docstruct`) as a .docx with python-docx."""

from __future__ import annotations

import os
import re

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ROW_HEIGHT_RULE, WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from .docstruct import Cell, PageDoc, Para, Table, _wide
from .models import Run

_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
CJK_DEFAULT = "Microsoft JhengHei"
LATIN_DEFAULT = "Calibri"


def _clean(t: str) -> str:
    return _BAD_XML.sub("", t)


def _half(pt: float) -> float:
    return max(1.0, round(pt * 2) / 2)


def _set_fonts(run, family: str | None, text: str) -> None:
    rpr = run._r.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    latin = family or LATIN_DEFAULT
    east = family or CJK_DEFAULT
    for attr in ("w:ascii", "w:hAnsi", "w:cs"):
        fonts.set(qn(attr), latin)
    fonts.set(qn("w:eastAsia"), east)
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        if fonts.get(qn(attr)) is not None:
            del fonts.attrib[qn(attr)]


def _add_run(par, r: Run, force_color: bool = False) -> None:
    text = _clean(r.text)
    if not text:
        return
    run = par.add_run(text)
    st = r.style
    _set_fonts(run, st.family, text)
    run.font.size = Pt(_half(st.size))
    if st.bold:
        run.font.bold = True
    elif force_color:  # a heading style is bold; the source heading is not
        run.font.bold = False
    if st.italic:
        run.font.italic = True
    col = st.color
    if force_color or max(col) > 70:
        run.font.color.rgb = RGBColor(*col) if max(col) > 70 else RGBColor(0, 0, 0)


def _format(par, p: Para, heading: bool = False) -> None:
    pf = par.paragraph_format
    pf.space_before = Pt(round(p.space_before, 1))
    pf.space_after = Pt(round(p.space_after, 1))
    if p.line_pitch > 0:
        pf.line_spacing_rule = WD_LINE_SPACING.AT_LEAST
        pf.line_spacing = Pt(round(max(p.line_pitch, p.size), 1))
    else:
        pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
    pf.alignment = {"center": WD_ALIGN_PARAGRAPH.CENTER, "right": WD_ALIGN_PARAGRAPH.RIGHT}.get(p.align, WD_ALIGN_PARAGRAPH.LEFT)
    if p.left_indent > 0.5:
        pf.left_indent = Pt(round(p.left_indent, 1))
    if p.right_indent > 0.5:
        pf.right_indent = Pt(round(p.right_indent, 1))
    if abs(p.first_line) > 0.5:
        pf.first_line_indent = Pt(round(p.first_line, 1))
    for pos, kind in p.tabs:
        if pos > 0:
            pf.tab_stops.add_tab_stop(Pt(round(pos, 1)), WD_TAB_ALIGNMENT.RIGHT if kind == "right" else WD_TAB_ALIGNMENT.LEFT)
    pf.widow_control = False


def _write_para(doc_or_cell, p: Para, par=None):
    style = f"Heading {p.level}" if p.kind == "heading" else None
    if par is None:
        par = doc_or_cell.add_paragraph(style=style)
    elif style:
        par.style = style
    _format(par, p, heading=p.kind == "heading")
    for r in p.runs:
        _add_run(par, r, force_color=p.kind == "heading")
    return par


def _tiny(par, size: float = 1.0) -> None:
    pf = par.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    pf.line_spacing = Pt(max(1.0, size))
    ppr = par._p.get_or_add_pPr()
    rpr = ppr.find(qn("w:rPr"))
    if rpr is None:
        rpr = OxmlElement("w:rPr")
        ppr.append(rpr)
    for tag in ("w:sz", "w:szCs"):
        e = OxmlElement(tag)
        e.set(qn("w:val"), str(max(2, int(size * 2))))
        rpr.append(e)


def _hex(c) -> str:
    return "%02X%02X%02X" % tuple(c)


def _shade(cell, color) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), _hex(color))
    tcPr.append(shd)


def _borders(cell, flags, sz: int, color) -> None:
    _borders_tc(cell._tc, flags, sz, color)


def _borders_tc(tc, flags, sz: int, color) -> None:
    tcPr = tc.get_or_add_tcPr()
    b = OxmlElement("w:tcBorders")
    for tag, on in zip(("top", "left", "bottom", "right"), flags):
        e = OxmlElement(f"w:{tag}")
        if on:
            e.set(qn("w:val"), "single")
            e.set(qn("w:sz"), str(sz))
            e.set(qn("w:space"), "0")
            e.set(qn("w:color"), _hex(color))
        else:
            e.set(qn("w:val"), "nil")
        b.append(e)
    tcPr.append(b)


def _tc_at(tbl, row: int, col: int):
    """The w:tc that starts at grid column ``col`` of ``row`` (horizontal merges shift the index)."""
    pos = 0
    for tc in tbl._tbl.tr_lst[row].tc_lst:
        if pos == col:
            return tc
        pos += tc.grid_span
    return None


def _table_props(tbl, width_pt: float, indent_pt: float) -> None:
    tblPr = tbl._tbl.tblPr
    for tag in ("w:tblW", "w:tblLayout", "w:tblInd", "w:tblCellMar", "w:tblBorders"):
        for e in tblPr.findall(qn(tag)):
            tblPr.remove(e)
    w = OxmlElement("w:tblW")
    w.set(qn("w:w"), str(int(width_pt * 20)))
    w.set(qn("w:type"), "dxa")
    tblPr.append(w)
    if indent_pt > 0.5:
        ind = OxmlElement("w:tblInd")
        ind.set(qn("w:w"), str(int(indent_pt * 20)))
        ind.set(qn("w:type"), "dxa")
        tblPr.append(ind)
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement(f"w:{side}")
        e.set(qn("w:val"), "nil")
        borders.append(e)
    tblPr.append(borders)
    lay = OxmlElement("w:tblLayout")
    lay.set(qn("w:type"), "fixed")
    tblPr.append(lay)
    mar = OxmlElement("w:tblCellMar")
    for side, v in (("top", 20), ("left", 50), ("bottom", 20), ("right", 50)):
        e = OxmlElement(f"w:{side}")
        e.set(qn("w:w"), str(v))
        e.set(qn("w:type"), "dxa")
        mar.append(e)
    tblPr.append(mar)


def _write_table(doc, t: Table) -> None:
    rows = len(t.row_h)
    cols = len(t.col_w)
    tbl = doc.add_table(rows=rows, cols=cols)
    tbl.alignment = WD_TABLE_ALIGNMENT.LEFT
    tbl.autofit = False
    _table_props(tbl, sum(t.col_w), t.left_indent)
    grid = tbl._tbl.tblGrid
    for gc, w in zip(grid.findall(qn("w:gridCol")), t.col_w):
        gc.set(qn("w:w"), str(int(w * 20)))
    for ri, h in enumerate(t.row_h):
        row = tbl.rows[ri]
        if h > 0:
            row.height = Pt(round(h, 1))
            row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST
        for ci, w in enumerate(t.col_w):
            tbl.cell(ri, ci).width = Pt(round(w, 1))

    origin = [c for c in t.cells]
    for c in origin:
        if c.rs > 1 or c.cs > 1:
            tbl.cell(c.r, c.c).merge(tbl.cell(c.r + c.rs - 1, c.c + c.cs - 1))

    sz = max(2, min(24, int(round(t.line_pt * 8))))
    for c in origin:
        cell = tbl.cell(c.r, c.c)
        if c.fill:
            _shade(cell, c.fill)
        if t.ruled:
            top, left, bottom, right = c.borders
            if c.rs > 1:
                bottom = False  # the bottom edge is drawn by the last row the cell spans
            _borders(cell, (top, left, bottom, right), sz, t.line_color)
            for rr in range(c.r + 1, c.r + c.rs):
                tc = _tc_at(tbl, rr, c.c)
                if tc is not None:
                    _borders_tc(tc, (False, left, c.borders[2] if rr == c.r + c.rs - 1 else False, right), sz, t.line_color)
        if c.valign == "center":
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        elif c.valign == "bottom":
            cell.vertical_alignment = WD_ALIGN_VERTICAL.BOTTOM
        if not c.paras:
            _tiny(cell.paragraphs[0], 6.0)
            continue
        first = True
        for p in c.paras:
            par = cell.paragraphs[0] if first else None
            first = False
            if par is None:
                par = cell.add_paragraph()
            _format(par, p)
            for r in p.runs:
                _add_run(par, r)


def _style_headings(doc) -> None:
    for lvl in range(1, 5):
        try:
            st = doc.styles[f"Heading {lvl}"]
        except KeyError:
            continue
        st.font.color.rgb = RGBColor(0, 0, 0)
        st.font.bold = True
        rpr = st.element.get_or_add_rPr()
        fonts = rpr.find(qn("w:rFonts"))
        if fonts is not None:
            for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
                if fonts.get(qn(attr)) is not None:
                    del fonts.attrib[qn(attr)]
        st.paragraph_format.keep_with_next = False
        st.paragraph_format.space_before = Pt(0)
        st.paragraph_format.space_after = Pt(0)


def write_docx(pages: list[PageDoc], out_path: str, title: str | None = None) -> dict:
    doc = Document()
    _style_headings(doc)
    normal = doc.styles["Normal"]
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.space_before = Pt(0)
    if title:
        doc.core_properties.title = title

    stats = {"paragraphs": 0, "headings": 0, "lists": 0, "tables": 0}
    for i, pg in enumerate(pages):
        if i == 0:
            sec = doc.sections[0]
        else:
            sec = doc.add_section(WD_SECTION.NEW_PAGE)
            _tiny(doc.paragraphs[-1])  # the section-break paragraph must not push a blank page
        sec.page_width = Pt(round(pg.width, 1))
        sec.page_height = Pt(round(pg.height, 1))
        l, t, r, b = pg.margins
        sec.left_margin, sec.top_margin = Pt(round(l, 1)), Pt(round(t, 1))
        sec.right_margin, sec.bottom_margin = Pt(round(r, 1)), Pt(round(b, 1))
        sec.header_distance = Pt(min(36.0, t / 2))
        sec.footer_distance = Pt(min(36.0, b / 2))

        last_table = False
        for el in pg.elements:
            if isinstance(el, Table):
                if last_table:  # two tables would fuse into one in Word
                    _tiny(doc.add_paragraph())
                _write_table(doc, el)
                stats["tables"] += 1
                last_table = True
            else:
                _write_para(doc, el)
                stats["paragraphs"] += 1
                stats["headings"] += el.kind == "heading"
                stats["lists"] += el.kind == "list"
                last_table = False
        if last_table and i == len(pages) - 1:
            _tiny(doc.add_paragraph())
        elif last_table:
            pass  # the next section break paragraph follows the table
    if not any(pg.elements for pg in pages):
        doc.add_paragraph("")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    tmp = out_path + ".tmp"
    doc.save(tmp)
    os.replace(tmp, out_path)
    return stats
