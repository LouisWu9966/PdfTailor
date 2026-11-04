"""Reads the real text layer of a page and turns it into editable lines.

Everything the editor needs to redraw a line "in the same voice" is available here: the
font each glyph was set in, its weight and italic angle, its size and its fill colour.
"""

from __future__ import annotations

import ctypes
import math
import unicodedata
from dataclasses import dataclass

import pypdfium2.raw as raw

from .fontnames import ParsedFont, decode_symbolic_char, is_private_use, parse_pdf_font_name
from .geometry import PageGeometry, Rect, UserRect
from .models import Block, Run, TextStyle
from .pdfio import PdfDoc

# FontDescriptor /Flags bits that matter here.
_FLAG_ITALIC = 0x40
_FLAG_FORCE_BOLD = 0x40000

# A gap wider than this many font sizes starts a new block (table cells, columns).
_BLOCK_SPLIT_EM = 2.2
# A gap wider than this many font sizes between glyphs reads as a space.
_SPACE_EM = 0.2
_CJK_SPACE_EM = 0.75


@dataclass
class Glyph:
    ch: str
    box: Rect  # display space
    size: float
    font_name: str
    parsed: ParsedFont
    weight: int
    italic_flag: bool
    bold_flag: bool
    color: tuple[int, int, int]
    invisible: bool
    upright: bool
    generated: bool
    unresolved_symbol: bool = False


@dataclass
class TextLayerStats:
    total: int = 0
    visible: int = 0
    invisible: int = 0
    skipped_rotated: int = 0
    unresolved_symbols: int = 0

    @property
    def kind(self) -> str:
        """``"none"``, ``"invisible"`` (an OCR layer hidden under a scan) or ``"visible"``."""
        if self.total == 0:
            return "none"
        if self.visible == 0:
            return "invisible"
        return "visible"


def is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x2E80 <= o <= 0x9FFF
        or 0xF900 <= o <= 0xFAFF
        or 0xFF00 <= o <= 0xFFEF
        or 0x3000 <= o <= 0x303F
        or 0x20000 <= o <= 0x2FFFF
    )


def _read_name(fn, handle) -> str:
    buf = ctypes.create_string_buffer(256)
    n = fn(handle, buf, len(buf))
    if n <= 0:
        return ""
    return buf.value.decode("utf-8", "replace")


def read_glyphs(doc: PdfDoc, page_number: int) -> tuple[list[Glyph], TextLayerStats]:
    geom = doc.geometry(page_number)
    page = doc.pdfium[page_number - 1]
    tp = page.get_textpage()
    stats = TextLayerStats()
    glyphs: list[Glyph] = []
    font_cache: dict[int, tuple[str, str, int, bool, bool]] = {}
    base_dx, base_dy = geom.baseline_matrix()
    # pdfium reports a glyph's angle clockwise, i.e. the negative of the usual
    # counter-clockwise convention (measured: /Rotate 90 -> 270, /Rotate 270 -> 90).
    want_angle = -math.atan2(base_dy, base_dx)

    try:
        n = tp.count_chars()
        i = 0
        while i < n:
            code = raw.FPDFText_GetUnicode(tp.raw, i)
            step = 1
            if 0xD800 <= code <= 0xDBFF and i + 1 < n:  # surrogate pair
                low = raw.FPDFText_GetUnicode(tp.raw, i + 1)
                if 0xDC00 <= low <= 0xDFFF:
                    code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                    step = 2
            idx = i
            i += step
            if code < 32 or code == 0xFFFE or code == 0xFFFF:
                continue  # line breaks and other controls pdfium injects
            generated = bool(raw.FPDFText_IsGenerated(tp.raw, idx))
            if generated:
                continue  # synthesised spaces: spacing is re-derived from real gaps

            left, bottom, right, top = tp.get_charbox(idx, loose=False)
            if right <= left or top <= bottom:
                if chr(code) != " ":
                    continue
            box = geom.user_rect_to_display(UserRect(left, bottom, right, top))

            size = float(raw.FPDFText_GetFontSize(tp.raw, idx))
            angle = float(raw.FPDFText_GetCharAngle(tp.raw, idx))
            diff = abs((angle - want_angle + math.pi) % (2 * math.pi) - math.pi)
            upright = diff < math.radians(12)

            textobj = raw.FPDFText_GetTextObject(tp.raw, idx)
            invisible = False
            font_name, family_name, weight, bold_flag, italic_flag = "", "", 400, False, False
            if textobj:
                mode = raw.FPDFTextObj_GetTextRenderMode(textobj)
                invisible = mode == 3  # FPDF_TEXTRENDERMODE_INVISIBLE
                font = raw.FPDFTextObj_GetFont(textobj)
                key = ctypes.cast(font, ctypes.c_void_p).value or 0
                if key not in font_cache:
                    flags = raw.FPDFFont_GetFlags(font)
                    angle_i = ctypes.c_int(0)
                    has_angle = raw.FPDFFont_GetItalicAngle(font, ctypes.byref(angle_i))
                    base = _read_name(raw.FPDFFont_GetBaseFontName, font)
                    fam = _read_name(raw.FPDFFont_GetFamilyName, font)
                    w = int(raw.FPDFFont_GetWeight(font))
                    ital = bool(flags & _FLAG_ITALIC) or (bool(has_angle) and angle_i.value != 0)
                    bold = bool(flags & _FLAG_FORCE_BOLD) or w >= 600
                    font_cache[key] = (base, fam, w, bold, ital)
                font_name, family_name, weight, bold_flag, italic_flag = font_cache[key]

            r, g, b, a = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
            if raw.FPDFText_GetFillColor(tp.raw, idx, r, g, b, a):
                color = (int(r.value), int(g.value), int(b.value))
            else:
                color = (0, 0, 0)

            parsed = parse_pdf_font_name(font_name or family_name)
            ch = chr(code)
            unresolved = False
            if parsed.symbolic or is_private_use(ch):
                decoded = decode_symbolic_char(ch, parsed.family)
                unresolved = is_private_use(decoded)
                ch = decoded

            stats.total += 1
            if invisible:
                stats.invisible += 1
            else:
                stats.visible += 1
            if not upright:
                stats.skipped_rotated += 1
            if unresolved:
                stats.unresolved_symbols += 1

            glyphs.append(
                Glyph(
                    ch=ch,
                    box=box,
                    size=size,
                    font_name=font_name,
                    parsed=parsed,
                    weight=weight,
                    italic_flag=italic_flag,
                    bold_flag=bold_flag,
                    color=color,
                    invisible=invisible,
                    upright=upright,
                    generated=False,
                    unresolved_symbol=unresolved,
                )
            )
    finally:
        tp.close()
    return glyphs, stats


def glyph_style(g: Glyph) -> TextStyle:
    return TextStyle(
        family=g.parsed.family or (g.parsed.hint or None),
        size=g.size,
        bold=g.parsed.bold or g.bold_flag,
        italic=g.parsed.italic or g.italic_flag,
        color=g.color,
        source="font",
    )


def _style_key(s: TextStyle) -> tuple:
    return (s.family or "", s.bold, s.italic, s.color, round(s.size * 2) / 2)


def _cluster_lines(glyphs: list[Glyph]) -> list[list[Glyph]]:
    """Groups glyphs into visual lines by vertical position."""
    if not glyphs:
        return []
    sizes = sorted(g.size for g in glyphs if g.size > 0)
    typical = sizes[len(sizes) // 2] if sizes else 10.0
    tol = max(1.5, typical * 0.5)
    ordered = sorted(glyphs, key=lambda g: g.box.cy)
    lines: list[list[Glyph]] = []
    line_cy = 0.0
    for g in ordered:
        if lines and abs(g.box.cy - line_cy) <= tol:
            lines[-1].append(g)
            line_cy = sum(x.box.cy for x in lines[-1]) / len(lines[-1])
        else:
            lines.append([g])
            line_cy = g.box.cy
    return lines


def _split_segments(line: list[Glyph]) -> list[list[Glyph]]:
    """Splits a line at wide gaps so table cells and columns stay separate blocks."""
    line = sorted(line, key=lambda g: g.box.x)
    segments: list[list[Glyph]] = [[line[0]]]
    for prev, cur in zip(line, line[1:]):
        size = max(prev.size, cur.size, 1.0)
        gap = cur.box.x - prev.box.right
        if gap > _BLOCK_SPLIT_EM * size:
            segments.append([cur])
        else:
            segments[-1].append(cur)
    return segments


def _needs_space(prev: Glyph, cur: Glyph) -> bool:
    if prev.ch == " " or cur.ch == " ":
        return False
    size = max(prev.size, cur.size, 1.0)
    gap = cur.box.x - prev.box.right
    if is_cjk(prev.ch) and is_cjk(cur.ch):
        return gap >= _CJK_SPACE_EM * size
    return gap >= _SPACE_EM * size


def build_blocks(glyphs: list[Glyph], page_number: int) -> list[Block]:
    usable = [g for g in glyphs if g.upright and not g.invisible]
    blocks: list[Block] = []
    next_id = 0
    for line in sorted(_cluster_lines(usable), key=lambda l: min(g.box.y for g in l)):
        for seg in _split_segments(line):
            text_parts: list[str] = []
            runs: list[Run] = []
            prev: Glyph | None = None
            for g in seg:
                piece = g.ch
                if prev is not None and _needs_space(prev, g):
                    piece = " " + piece
                style = glyph_style(g)
                x0 = g.box.x
                if runs and _style_key(runs[-1].style) == _style_key(style):
                    runs[-1].text += piece
                    runs[-1].x1 = g.box.right
                else:
                    runs.append(Run(piece, style, x0, g.box.right))
                text_parts.append(piece)
                prev = g

            # Trim at the run level so block.text always equals the runs joined together.
            while runs and not runs[0].text.strip():
                runs.pop(0)
            while runs and not runs[-1].text.strip():
                runs.pop()
            if not runs:
                continue
            runs[0].text = runs[0].text.lstrip()
            runs[-1].text = runs[-1].text.rstrip()
            text = "".join(r.text for r in runs)
            if not text.strip():
                continue
            left = min(g.box.x for g in seg)
            top = min(g.box.y for g in seg)
            right = max(g.box.right for g in seg)
            bottom = max(g.box.bottom for g in seg)

            # The dominant run (by character count) speaks for the whole line.
            weight: dict[tuple, int] = {}
            by_key: dict[tuple, TextStyle] = {}
            for r in runs:
                k = _style_key(r.style)
                weight[k] = weight.get(k, 0) + len(r.text.strip())
                by_key[k] = r.style
            dominant = by_key[max(weight, key=weight.get)]

            block = Block(
                id=next_id,
                page=page_number,
                text=text,
                bounds=Rect.from_bounds(left, top, right, bottom),
                source="text",
                style=dominant,
                runs=runs,
            )
            if any(g.unresolved_symbol for g in seg):
                block.notes.append("含無法對應的私用區符號，輸出時可能顯示成空白或方框。")
            blocks.append(block)
            next_id += 1
    return blocks


def count_text_stats(doc: PdfDoc, page_number: int) -> TextLayerStats:
    """Cheap classification of a page's text layer without building blocks."""
    _, stats = read_glyphs(doc, page_number)
    return stats
