"""Turns the lines of PDF pages into a flowing document: paragraphs, headings, lists, tables.

Input is what the analysis already produces — one :class:`Block` per line (or per cell-sized
segment of a line) with a box in display points — plus the ruled grids found by
:mod:`tables`. Output is a list of :class:`PageDoc`, each an ordered list of
:class:`Para` and :class:`Table` that :mod:`docxout` writes as Word.

Nothing here knows about Word; everything is plain geometry and text rules, so it can be
tested without a document.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field, replace

import numpy as np

from .geometry import Rect
from .models import RGB, Block, Run, TextStyle
from .tables import Grid

# --------------------------------------------------------------------------- data

@dataclass
class Line:
    runs: list[Run]
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    bold_frac: float
    text: str
    parts: list[tuple[float, float]]  # x extent of each block that makes the line
    tab_before: list[int] = field(default_factory=list)  # indices into parts that follow a TAB

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass
class Para:
    runs: list[Run]
    text: str
    kind: str = "body"  # body | heading | list
    level: int = 0
    align: str = "left"
    left_indent: float = 0.0
    first_line: float = 0.0  # relative to left_indent; negative = hanging
    right_indent: float = 0.0
    space_before: float = 0.0
    space_after: float = 0.0
    line_pitch: float = 0.0  # > 0 for multi-line paragraphs
    size: float = 10.5
    bold_frac: float = 0.0
    tabs: list[tuple[float, str]] = field(default_factory=list)  # (pt from left margin, left|right)
    marker: str | None = None
    in_cell: bool = False
    # geometry kept for the later passes
    top: float = 0.0
    bottom: float = 0.0
    x0: float = 0.0
    x1: float = 0.0
    n_lines: int = 1
    body_left: float = 0.0
    first_left: float = 0.0
    area_left: float = 0.0
    hang_exact: bool = False
    marker_sep: str = " "


@dataclass
class Cell:
    r: int
    c: int
    rs: int
    cs: int
    paras: list[Para] = field(default_factory=list)
    fill: RGB | None = None
    valign: str = "top"
    borders: tuple[bool, bool, bool, bool] = (False, False, False, False)  # top, left, bottom, right


@dataclass
class Table:
    bounds: Rect
    col_w: list[float]
    row_h: list[float]
    cells: list[Cell]
    ruled: bool
    line_pt: float = 0.5
    line_color: RGB = (0, 0, 0)
    left_indent: float = 0.0
    space_before: float = 0.0

    @property
    def top(self) -> float:
        return self.bounds.y

    @property
    def bottom(self) -> float:
        return self.bounds.bottom


@dataclass
class PageInput:
    page: int
    width: float
    height: float
    blocks: list[Block]
    grids: list[Grid] = field(default_factory=list)
    rgb: np.ndarray | None = None
    scale: float = 1.0


@dataclass
class PageDoc:
    page: int
    width: float
    height: float
    margins: tuple[float, float, float, float]  # left, top, right, bottom
    elements: list = field(default_factory=list)  # Para | Table


# --------------------------------------------------------------------------- text helpers

def _wide(ch: str) -> bool:
    o = ord(ch)
    return o >= 0x2E80 and not (0xFE00 <= o <= 0xFE0F)


_END_PUNCT = "。！？；：.!?;:"
_BULLETS = "•·●○■□◆◇▪▫◦‣⁃▶►➢➤✓✔※★☆"
_BULLET_RE = re.compile(r"^([" + re.escape(_BULLETS) + r"]|[-–—*])\s+(?=\S)|^([" + re.escape(_BULLETS) + r"])(?=\S)")
_NUM_RE = re.compile(
    r"^(?:"
    r"[（(]\d{1,3}[)）]"  # (1) （1）
    r"|\d{1,3}(?:\.\d{1,3})+\.?(?=\s)"  # 1.1  1.2.3
    r"|\d{1,3}[\.\)）、](?!\d)"  # 1. 1) 1、
    r"|[（(][一二三四五六七八九十]{1,3}[)）]"  # （一）
    r"|[一二三四五六七八九十]{1,3}[、．]"  # 一、
    r"|[（(][A-Za-z][)）]"  # (a)
    r"|[A-Za-z][\.\)](?=\s)"  # a.  A)
    r"|[ivxIVX]{1,5}[\.\)](?=\s)"  # i. iv)
    r"|[①-⑳㈠-㈩⑴-⒇]"
    r")\s*"
)
_HEAD_NUM_RE = re.compile(r"^(?:第[一二三四五六七八九十百零\d]+[章節节條条款項项部篇]|\d{1,2}(?:\.\d{1,2})*[\.、\s]|[一二三四五六七八九十]{1,3}[、．]|[（(][一二三四五六七八九十]{1,3}[)）])")


def match_marker(text: str) -> tuple[str, str] | None:
    """Returns (marker as written, normalised marker) when ``text`` begins a list item."""
    m = _BULLET_RE.match(text)
    if m:
        raw = m.group(0).rstrip()
        return raw, "•"
    m = _NUM_RE.match(text)
    if m:
        raw = m.group(0).rstrip()
        rest = text[len(m.group(0)):]
        if not rest.strip():
            return None
        return raw, raw
    return None


def _join_needs_space(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a[-1].isspace() or b[0].isspace():
        return False
    if _wide(a[-1]) or _wide(b[0]):
        return False
    if a[-1] == "-" and a[-2:-1].isalpha():
        return False
    return True


# --------------------------------------------------------------------------- lines

def group_rows(blocks: list[Block]) -> list[list[Block]]:
    items = sorted(blocks, key=lambda b: (b.bounds.cy, b.bounds.x))
    rows: list[list[Block]] = []
    for b in items:
        if rows:
            r = rows[-1]
            top = min(x.bounds.y for x in r)
            bot = max(x.bounds.bottom for x in r)
            ov = min(bot, b.bounds.bottom) - max(top, b.bounds.y)
            if ov >= 0.5 * min(bot - top, b.bounds.h):
                r.append(b)
                continue
        rows.append([b])
    for r in rows:
        r.sort(key=lambda b: b.bounds.x)
    return rows


def _bold_frac(runs: list[Run]) -> float:
    tot = sum(len(r.text.strip()) for r in runs)
    if not tot:
        return 0.0
    return sum(len(r.text.strip()) for r in runs if r.style.bold) / tot


def _dominant_size(runs: list[Run]) -> float:
    w: dict[float, int] = {}
    for r in runs:
        k = round(r.style.size * 2) / 2
        w[k] = w.get(k, 0) + len(r.text.strip())
    return max(w, key=w.get) if w else 10.5


def _copy_runs(b: Block) -> list[Run]:
    runs = b.runs or [Run(b.text, b.style, b.bounds.x, b.bounds.right)]
    return [Run(r.text, r.style, r.x0, r.x1) for r in runs]


def make_line(row: list[Block], joiner: str = "\t") -> Line:
    runs: list[Run] = []
    parts: list[tuple[float, float]] = []
    tab_before: list[int] = []
    for i, b in enumerate(row):
        if i > 0:
            style = runs[-1].style if runs else b.style
            runs.append(Run(joiner, style, b.bounds.x, b.bounds.x))
            if joiner == "\t":
                tab_before.append(i)
        runs.extend(_copy_runs(b))
        parts.append((b.bounds.x, b.bounds.right))
    text = "".join(r.text for r in runs)
    return Line(
        runs=runs,
        x0=min(b.bounds.x for b in row),
        x1=max(b.bounds.right for b in row),
        top=min(b.bounds.y for b in row),
        bottom=max(b.bounds.bottom for b in row),
        size=_dominant_size(runs),
        bold_frac=_bold_frac(runs),
        text=text,
        parts=parts,
        tab_before=tab_before,
    )


# --------------------------------------------------------------------------- paragraphs

@dataclass
class _Acc:
    lines: list[Line]
    marker: tuple[str, str] | None = None

    @property
    def body_left(self) -> float:
        return self.lines[1].x0 if len(self.lines) > 1 else self.lines[0].x0


def _first_token_width(line: Line) -> float:
    t = line.text.lstrip()
    if not t:
        return 0.0
    if _wide(t[0]):
        return line.size
    m = re.match(r"\S+", t)
    n = len(m.group()) if m else 1
    avg = (line.x1 - line.x0) / max(1, len(line.text))
    return n * avg + 0.3 * line.size


def _starts_new(acc: _Acc, cur: Line, R: float) -> bool:
    prev = acc.lines[-1]
    size = max(prev.size, cur.size)
    pitch = cur.cy - prev.cy
    if pitch > 1.75 * size + 1.0 or pitch < 0.55 * size:
        return True
    if abs(prev.size - cur.size) > max(0.6, 0.06 * size):
        return True
    if abs(prev.bold_frac - cur.bold_frac) > 0.8:
        return True
    if prev.tab_before or cur.tab_before:
        return True
    if match_marker(cur.text) is not None:
        return True
    # was the previous line really filled to the edge, i.e. would the next word have fit?
    slack = R - prev.x1
    if slack > _first_token_width(cur) + 0.5 * size:
        return True
    if prev.text.rstrip()[-1:] in "。" and slack > 0.5 * size and len(acc.lines) >= 1 and slack > 1.2 * size:
        return True
    ref = prev.x0 if len(acc.lines) == 1 else acc.body_left
    d = cur.x0 - ref
    tol = 1.3 * size
    if acc.marker is not None:
        if len(acc.lines) == 1:
            return d < -tol  # hanging wrap may be indented or flush, never outdented
        return abs(d) > tol
    if d > tol:
        return True
    if d < -tol:
        # first-line indent ("　　" in Chinese text): the 2nd line returns to the margin
        return len(acc.lines) >= 2
    return False


def build_paragraphs(lines: list[Line], L: float, R: float, in_cell: bool = False) -> list[Para]:
    if not lines:
        return []
    groups: list[_Acc] = []
    for ln in lines:
        mk = match_marker(ln.text)
        if groups and not _starts_new(groups[-1], ln, R):
            groups[-1].lines.append(ln)
        else:
            groups.append(_Acc([ln], mk))
    return [_finish(g, L, R, in_cell) for g in groups]


def _squeeze_cjk_spaces(runs: list[Run]) -> None:
    """Drops spaces between two wide characters (CJK text has none; the gap is a glyph-box artefact)."""
    chars = [(ri, ci) for ri, r in enumerate(runs) for ci in range(len(r.text))]
    flat = "".join(r.text for r in runs)
    drop: set[tuple[int, int]] = set()
    for i, ch in enumerate(flat):
        if ch == " " and 0 < i < len(flat) - 1 and _wide(flat[i - 1]) and _wide(flat[i + 1]):
            drop.add(chars[i])
    if not drop:
        return
    for ri, r in enumerate(runs):
        r.text = "".join(ch for ci, ch in enumerate(r.text) if (ri, ci) not in drop)


def _finish(acc: _Acc, L: float, R: float, in_cell: bool) -> Para:
    lines = acc.lines
    runs: list[Run] = []
    for i, ln in enumerate(lines):
        if i > 0 and runs and ln.runs and _join_needs_space(runs[-1].text, ln.runs[0].text):
            runs.append(Run(" ", runs[-1].style, 0, 0))
        runs.extend(Run(r.text, r.style, r.x0, r.x1) for r in ln.runs)

    _squeeze_cjk_spaces(runs)
    first = lines[0]
    size = _dominant_size(runs)
    para = Para(
        runs=runs, text="".join(r.text for r in runs), size=size, bold_frac=_bold_frac(runs),
        top=first.top, bottom=lines[-1].bottom, x0=min(l.x0 for l in lines), x1=max(l.x1 for l in lines),
        n_lines=len(lines), in_cell=in_cell,
    )
    if len(lines) > 1:
        ps = [lines[i + 1].cy - lines[i].cy for i in range(len(lines) - 1)]
        para.line_pitch = float(np.median(ps))

    # tab stops for multi-block lines (only single-line paragraphs get them)
    if len(lines) == 1 and first.tab_before:
        for i in first.tab_before:
            x0, x1 = first.parts[i]
            if x1 >= R - 0.5 * size and x0 - first.parts[i - 1][1] > size:
                para.tabs.append((x1 - L, "right"))
            else:
                para.tabs.append((x0 - L, "left"))

    # list item: literal marker + TAB, hanging indent
    if acc.marker is not None:
        raw, norm = acc.marker
        r0 = runs[0]
        if r0.text.startswith(raw):
            tail = r0.text[len(raw):].lstrip()
            para.marker_sep = " " if r0.text[len(raw):][:1].isspace() else ""
            r0.text = norm + "\t" + tail
            para.marker = norm
            para.text = "".join(r.text for r in runs)
            if len(lines) > 1 and lines[1].x0 > first.x0 + 0.5 * size:
                text_x = lines[1].x0
                para.hang_exact = True
            elif first.tab_before and first.parts[0][1] - first.parts[0][0] < 4.5 * size:
                text_x = first.parts[1][0]
                para.hang_exact = True
                para.tabs = [t for t in para.tabs if abs(t[0] - (text_x - L)) > 1]
            else:
                width = sum(1.0 if _wide(ch) else 0.55 for ch in raw) * size
                text_x = first.x0 + max(1.2 * size, width + 0.4 * size)
            para.kind = "list"
            para.first_left = first.x0
            para.body_left = text_x
            return para

    para.first_left = first.x0
    para.body_left = lines[1].x0 if len(lines) > 1 else first.x0
    return para


# --------------------------------------------------------------------------- borderless tables

def _row_extent(row: list[Block]) -> tuple[float, float]:
    return min(b.bounds.y for b in row), max(b.bounds.bottom for b in row)


def _row_size(row: list[Block]) -> float:
    return float(np.median([b.style.size for b in row]))


def _column_intervals(rows: list[list[Block]], n: int) -> list[tuple[float, float]] | None:
    iv = sorted((b.bounds.x, b.bounds.right) for r in rows if len(r) == n for b in r)
    merged: list[list[float]] = []
    for a, b in iv:
        if merged and a <= merged[-1][1] + 2.0:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    if len(merged) != n:
        return None
    if any(merged[i + 1][0] - merged[i][1] < 4.0 for i in range(n - 1)):
        return None
    return [(a, b) for a, b in merged]


def _row_cy(row: list[Block]) -> float:
    t, b = _row_extent(row)
    return (t + b) / 2


def detect_text_tables(rows: list[list[Block]], max_right: float = 1e9) -> list[tuple[int, int, Table]]:
    """Borderless tables: consecutive rows whose blocks line up in columns."""
    found: list[tuple[int, int, Table]] = []
    i = 0
    n = len(rows)
    while i < n:
        if len(rows[i]) < 2:
            i += 1
            continue
        end = i
        k = i + 1
        while k < n:
            prev, cur = rows[k - 1], rows[k]
            size = max(_row_size(prev), _row_size(cur))
            ratio = _row_size(cur) / max(0.1, _row_size(prev))
            pitch = _row_cy(cur) - _row_cy(prev)
            if not 0.8 <= ratio <= 1.25:
                break
            if len(cur) >= 2:
                if pitch > 2.4 * size:
                    break
                end = k
            elif pitch > 1.7 * size:
                break
            k += 1
        tbl, used_end = None, end
        while used_end > i:
            run = rows[i:used_end + 1]
            multi = [r for r in run if len(r) >= 2]
            if len(multi) < 2:
                break
            mode = Counter(len(r) for r in multi).most_common(1)[0][0]
            cols = _column_intervals(multi, mode)
            if cols is None:
                break
            bad = _first_spanning_single(run, cols)
            if bad is None:
                tbl = _build_text_table(run, cols, max_right)
                break
            used_end = i + bad - 1
            while used_end > i and len(rows[used_end]) < 2:
                used_end -= 1
        if tbl is not None:
            found.append((i, used_end, tbl))
            i = used_end + 1
        else:
            i += 1
    return found


def _first_spanning_single(run: list[list[Block]], cols: list[tuple[float, float]]) -> int | None:
    """Index of the first single-block row that is not confined to one column."""
    bounds = [cols[0][0] - 2.0] + [(a[1] + b[0]) / 2 for a, b in zip(cols, cols[1:])] + [cols[-1][1] + 2.0]
    for idx, r in enumerate(run):
        if len(r) != 1:
            continue
        blk = r[0]
        c0 = next((c for c in range(len(cols)) if blk.bounds.x + 0.5 < bounds[c + 1]), len(cols) - 1)
        c1 = next((c for c in range(len(cols)) if blk.bounds.right - 0.5 < bounds[c + 1]), len(cols) - 1)
        if c0 != c1:
            return idx
    return None


def _build_text_table(rows: list[list[Block]], cols: list[tuple[float, float]], max_right: float = 1e9) -> Table | None:
    n = len(cols)
    bounds_x = [cols[0][0] - 2.0]
    for a, b in zip(cols, cols[1:]):
        bounds_x.append((a[1] + b[0]) / 2)
    bounds_x.append(max(cols[-1][1] + 2.0, min(cols[-1][1] + 16.0, max_right)))  # room for wider Word fonts

    def col_of(x: float) -> int:
        for c in range(n):
            if x < bounds_x[c + 1]:
                return c
        return n - 1

    # table rows, each: {(c0, c1): [blocks]}
    trows: list[dict[tuple[int, int], list[Block]]] = []
    row_top: list[float] = []
    row_bottom: list[float] = []
    prev_ext: tuple[float, float] | None = None
    for r in rows:
        t, b = _row_extent(r)
        size = _row_size(r)
        spans = []
        for blk in r:
            c0 = col_of(blk.bounds.x + 0.5)
            c1 = col_of(blk.bounds.right - 0.5)
            spans.append(((c0, c1), blk))
        tight = prev_ext is not None and (t + b) / 2 - (prev_ext[0] + prev_ext[1]) / 2 <= 1.35 * size
        cont = tight and len(r) < n and all(s[0][0] == s[0][1] for s in spans) and trows
        if cont:
            for key, blk in spans:
                trows[-1].setdefault(key, []).append(blk)
            row_bottom[-1] = max(row_bottom[-1], b)
        else:
            d: dict[tuple[int, int], list[Block]] = {}
            for key, blk in spans:
                d.setdefault(key, []).append(blk)
            trows.append(d)
            row_top.append(t)
            row_bottom.append(b)
        prev_ext = (t, b)

    blocks_all = [blk for d in trows for bl in d.values() for blk in bl]
    avg_chars = sum(len(b.text) for b in blocks_all) / max(1, len(blocks_all))
    tight_all = len(trows) >= 4 and all(
        row_top[i + 1] - row_top[i] <= 1.5 * max(b.style.size for b in blocks_all)
        for i in range(len(trows) - 1))
    if tight_all and avg_chars >= 25:
        # newspaper-style columns of running text, not a table: one row, a cell per column
        merged_row: dict[tuple[int, int], list[Block]] = {}
        for d in trows:
            for key, bl in d.items():
                merged_row.setdefault(key, []).extend(bl)
        trows = [merged_row]
        row_top, row_bottom = [row_top[0]], [row_bottom[-1]]

    cells: list[Cell] = []
    for ri, d in enumerate(trows):
        occupied = set()
        for (c0, c1), blks in d.items():
            cell = Cell(ri, c0, 1, c1 - c0 + 1)
            L, R = cols[c0][0], cols[c1][1]  # where the text really sits
            cell.paras = _cell_paragraphs(blks, L, R)
            for p in cell.paras:  # carry the gap between text and cell edge as indent
                if p.align == "left":
                    p.left_indent += max(0.0, L - (bounds_x[c0] + 2.5))
                elif p.align == "right":
                    p.right_indent = max(0.0, (bounds_x[c1 + 1] - 2.5) - R)
            cells.append(cell)
            occupied.update(range(c0, c1 + 1))
        for c in range(n):
            if c not in occupied:
                cells.append(Cell(ri, c, 1, 1))
    cells.sort(key=lambda c: (c.r, c.c))
    # heights: pitch-to-pitch
    row_h = []
    for ri in range(len(trows)):
        nxt = row_top[ri + 1] if ri + 1 < len(trows) else row_bottom[ri]
        row_h.append(max(0.0, nxt - row_top[ri]))
    bounds = Rect.from_bounds(bounds_x[0], row_top[0], bounds_x[-1], row_bottom[-1])
    return Table(bounds, [b - a for a, b in zip(bounds_x, bounds_x[1:])], row_h, cells, ruled=False)


def _cell_paragraphs(blocks: list[Block], L: float, R: float) -> list[Para]:
    rows = group_rows(blocks)
    lines = [make_line(r, joiner=" ") for r in rows]
    paras = build_paragraphs(lines, L, R, in_cell=True)
    _harmonize_lists(paras)
    _align(paras, L, R, cell=True)
    _spacing(paras)
    return paras


# --------------------------------------------------------------------------- ruled tables

def _cells_from_grid(g: Grid) -> list[tuple[int, int, int, int]]:
    """Merged-cell rectangles (r, c, rowspan, colspan) from missing separators."""
    R, C = g.rows, g.cols
    parent = list(range(R * C))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    for r in range(R):
        for c in range(C - 1):
            if not g.vsep[r][c]:
                union(r * C + c, r * C + c + 1)
    for r in range(R - 1):
        for c in range(C):
            if not g.hsep[r][c]:
                union(r * C + c, (r + 1) * C + c)
    comps: dict[int, list[int]] = {}
    for i in range(R * C):
        comps.setdefault(find(i), []).append(i)
    out = []
    for members in comps.values():
        rs = [m // C for m in members]
        cs = [m % C for m in members]
        r0, r1, c0, c1 = min(rs), max(rs), min(cs), max(cs)
        if (r1 - r0 + 1) * (c1 - c0 + 1) == len(members):
            out.append((r0, c0, r1 - r0 + 1, c1 - c0 + 1))
        else:  # not a rectangle: keep the cells apart
            out.extend((m // C, m % C, 1, 1) for m in members)
    out.sort()
    return out


def _edge(vals: list[bool]) -> bool:
    return bool(vals) and sum(vals) * 2 >= len(vals)


_RULE_CHARS = " |¦│┃｜"


def _strip_rule_noise(b: Block) -> Block | None:
    """OCR reads a table's vertical rules as '|' at the edges of a cell's text; drop them."""
    if b.source != "ocr" or b.text == b.text.strip(_RULE_CHARS):
        return b
    runs = [Run(r.text, r.style, r.x0, r.x1) for r in b.runs] or [Run(b.text, b.style, b.bounds.x, b.bounds.right)]
    while runs and not runs[0].text.lstrip(_RULE_CHARS):
        runs.pop(0)
    while runs and not runs[-1].text.rstrip(_RULE_CHARS):
        runs.pop()
    if not runs:
        return None
    runs[0].text = runs[0].text.lstrip(_RULE_CHARS)
    runs[-1].text = runs[-1].text.rstrip(_RULE_CHARS)
    text = "".join(r.text for r in runs)
    return replace(b, text=text, runs=runs) if text.strip() else None


def build_ruled_table(g: Grid, blocks: list[Block], rgb: np.ndarray | None, scale: float) -> tuple[Table, list[Block]]:
    """Fills a grid with the blocks whose centres fall inside it. Returns (table, blocks used)."""
    rects = _cells_from_grid(g)
    cells: list[Cell] = []
    by_cell: dict[int, list[Block]] = {i: [] for i in range(len(rects))}
    inner = g.bounds.inflate(1.5)
    used: list[Block] = []

    def locate(px: float, py: float) -> int:
        best, best_d = 0, 1e18
        for i, (r, c, rs, cs) in enumerate(rects):
            x0, x1, y0, y1 = g.xs[c], g.xs[c + cs], g.ys[r], g.ys[r + rs]
            if x0 <= px <= x1 and y0 <= py <= y1:
                return i
            dx = max(x0 - px, 0, px - x1)
            dy = max(y0 - py, 0, py - y1)
            d = dx * dx + dy * dy
            if d < best_d:
                best, best_d = i, d
        return best

    for b in blocks:
        if inner.contains_point(b.bounds.cx, b.bounds.cy):
            used.append(b)
            clean = _strip_rule_noise(b)
            if clean is not None:
                by_cell[locate(b.bounds.cx, b.bounds.cy)].append(clean)

    for i, (r, c, rs, cs) in enumerate(rects):
        x0, x1, y0, y1 = g.xs[c], g.xs[c + cs], g.ys[r], g.ys[r + rs]
        pad = 2.5
        cell = Cell(r, c, rs, cs)
        blks = by_cell[i]
        if blks:
            cell.paras = _cell_paragraphs(blks, x0 + pad, x1 - pad)
            ct = min(b.bounds.y for b in blks)
            cb = max(b.bounds.bottom for b in blks)
            room_top, room_bot = ct - y0, y1 - cb
            if abs(room_top - room_bot) <= 0.25 * (y1 - y0) and (room_top + room_bot) > 6:
                cell.valign = "center"
            elif room_bot < 3 and room_top > 6:
                cell.valign = "bottom"
        cell.borders = (
            True if r == 0 else _edge([g.hsep[r - 1][cc] for cc in range(c, c + cs)]),
            True if c == 0 else _edge([g.vsep[rr][c - 1] for rr in range(r, r + rs)]),
            True if r + rs == g.rows else _edge([g.hsep[r + rs - 1][cc] for cc in range(c, c + cs)]),
            True if c + cs == g.cols else _edge([g.vsep[rr][c + cs - 1] for rr in range(r, r + rs)]),
        )
        if rgb is not None:
            cell.fill = _sample_fill(rgb, scale, x0, y0, x1, y1)
        cells.append(cell)

    table = Table(
        bounds=g.bounds,
        col_w=[b - a for a, b in zip(g.xs, g.xs[1:])],
        row_h=[b - a for a, b in zip(g.ys, g.ys[1:])],
        cells=cells,
        ruled=True,
        line_pt=g.line_pt,
        line_color=g.line_color,
    )
    return table, used


def _sample_fill(rgb: np.ndarray, scale: float, x0: float, y0: float, x1: float, y1: float) -> RGB | None:
    H, W = rgb.shape[:2]
    m = 2.5 * scale
    a, b = int(x0 * scale + m), int(y0 * scale + m)
    c, d = int(x1 * scale - m), int(y1 * scale - m)
    a, c = max(0, a), min(W, c)
    b, d = max(0, b), min(H, d)
    if c - a < 3 or d - b < 3:
        return None
    px = rgb[b:d, a:c].reshape(-1, 3)
    # the most common colour of the cell interior, which is the fill rather than the text
    q = (px // 8).astype(np.int32)
    key = q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]
    top = np.bincount(key).argmax()
    col = px[key == top].mean(axis=0)
    r, g_, b_ = (int(round(v)) for v in col)
    if min(r, g_, b_) >= 244:
        return None
    return (r, g_, b_)


# --------------------------------------------------------------------------- alignment & spacing

def _align(paras: list[Para], L: float, R: float, cell: bool = False, page_cx: float | None = None) -> None:
    w = max(1.0, R - L)
    for p in paras:
        p.area_left = L
        size = p.size
        slack_l = p.x0 - L
        slack_r = R - p.x1
        cx_off = abs((p.x0 + p.x1) / 2 - (L + R) / 2)
        if page_cx is not None:
            cx_off = min(cx_off, abs((p.x0 + p.x1) / 2 - page_cx))
        if cell:
            ctol = 0.06 * w + 1.5
            rtol = 0.04 * w + 2.0
        else:
            ctol = 0.02 * w + 2.0
            rtol = 0.015 * w + 2.0
        p.align = "left"
        if p.kind == "list" or p.tabs:
            pass
        elif slack_l > 1.5 * size and slack_r > 1.5 * size and cx_off <= ctol:
            p.align = "center"
        elif slack_r <= rtol and (slack_l > 0.9 * size if cell else (slack_l > 0.15 * w and slack_l > 2 * size)):
            p.align = "right"
        if p.align != "left":
            p.left_indent = 0.0
            p.right_indent = 0.0
            p.first_line = 0.0
            if p.align == "center":  # Word centres in the text area: shift if the text sits off-centre
                delta = (p.x0 + p.x1) / 2 - (L + R) / 2
                if delta > 1.0:
                    p.left_indent = 2 * delta
                elif delta < -1.0:
                    p.right_indent = -2 * delta
            continue
        if p.kind == "list":
            p.left_indent = max(0.0, p.body_left - L)
            p.first_line = -(p.body_left - p.first_left)
        else:
            p.left_indent = max(0.0, p.body_left - L)
            p.first_line = p.first_left - p.body_left if p.n_lines > 1 else 0.0
        if p.left_indent < 0.6 * size and p.kind != "list":
            p.left_indent = 0.0
        if abs(p.first_line) < 0.6 * size and p.kind != "list":
            p.first_line = 0.0
        if cell:
            p.left_indent = 0.0 if p.left_indent < 4 else p.left_indent


def _harmonize_lists(paras: list[Para]) -> None:
    """Items of one list share one text start, whatever their individual estimates were."""
    i = 0
    while i < len(paras):
        if paras[i].kind != "list":
            i += 1
            continue
        j = i
        while j + 1 < len(paras) and paras[j + 1].kind == "list" and abs(paras[j + 1].first_left - paras[i].first_left) <= 3.0:
            j += 1
        grp = paras[i:j + 1]
        exact = [p.body_left for p in grp if p.hang_exact]
        src = exact or [p.body_left for p in grp]
        x = float(np.median(src))
        for p in grp:
            p.body_left = x
            p.first_left = float(np.median([q.first_left for q in grp]))
        i = j + 1


def _spacing(paras: list[Para]) -> None:
    for a, b in zip(paras, paras[1:]):
        pitch = (b.top + b.bottom) / 2 - (a.top + a.bottom) / 2
        extra = pitch - 1.25 * max(a.size, b.size) - (b.n_lines - 1) * 0
        # pitch measured between the centre of the previous paragraph's last line and of the
        # next paragraph's first line, so the previous paragraph's own height must be removed
        last_cy = a.bottom - a.size * 0.5
        first_cy = b.top + b.size * 0.5
        extra = (first_cy - last_cy) - 1.25 * max(a.size, b.size)
        b.space_before = extra if extra >= 2.0 else 0.0
        b.space_before = min(b.space_before, 48.0)


# --------------------------------------------------------------------------- page assembly

def _region(lines: list[Line]) -> tuple[float, float]:
    """Left and right edge of the text column from where its lines start and end."""
    if not lines:
        return 0.0, 0.0
    body = [l for l in lines if len(l.text.strip()) >= 8] or lines
    weights: dict[int, float] = {}
    for l in body:
        weights[int(round(l.x0 / 3))] = weights.get(int(round(l.x0 / 3)), 0) + len(l.text)
    total = sum(weights.values())
    top = max(weights.values())
    lefts = [k for k, v in weights.items() if v >= 0.15 * total or v == top]
    L = min(lefts) * 3.0
    x1s = sorted(l.x1 for l in body)
    R = x1s[min(len(x1s) - 1, int(0.9 * len(x1s)))]
    return L, max(R, L + 1)


def _page_chunks(page: PageInput) -> tuple[list, tuple[float, float, float, float]]:
    """Ordered Para/Table elements of one page and the page's content box."""
    grids = _outer_grids(page.grids)
    tables: list[Table] = []
    taken: set[int] = set()
    for g in grids:
        t, used = build_ruled_table(g, page.blocks, page.rgb, page.scale)
        tables.append(t)
        taken.update(id(b) for b in used)
    free = [b for b in page.blocks if id(b) not in taken and b.text.strip()]

    rows = group_rows(free)
    consumed = [False] * len(rows)
    for (s, e, tbl) in detect_text_tables(rows, page.width - 14.0):
        for k in range(s, e + 1):
            consumed[k] = True
        tables.append(tbl)

    # content box for margins and alignment
    xs0, xs1, ys0, ys1 = [], [], [], []
    for b in free:
        xs0.append(b.bounds.x); xs1.append(b.bounds.right); ys0.append(b.bounds.y); ys1.append(b.bounds.bottom)
    for t in tables:
        xs0.append(t.bounds.x); xs1.append(t.bounds.right); ys0.append(t.bounds.y); ys1.append(t.bounds.bottom)
    if not xs0:
        return [], (36.0, 36.0, 36.0, 36.0)
    ml = min(max(min(xs0), 14.0), page.width / 3)
    mr = min(max(page.width - max(xs1), 14.0), page.width / 3)
    mt = min(max(min(ys0), 14.0), page.height / 3)
    mb = min(max(page.height - max(ys1), 14.0), page.height / 3)
    L_area, R_area = ml, page.width - mr

    free_rows = [r for r, used in zip(rows, consumed) if not used]
    lines_all = [make_line(r) for r in free_rows]
    Lp, Rp = _region(lines_all)
    Rp = max(Rp, min(R_area, Rp))

    # split the text rows around tables so paragraphs never run through one
    items: list[tuple[float, str, object]] = []
    for r, ln, used in zip(rows, [make_line(r) for r in rows], consumed):
        if not used:
            items.append((ln.top, "line", ln))
    for t in tables:
        items.append((t.top, "table", t))
    items.sort(key=lambda it: (it[0], 0 if it[1] == "table" else 1))

    elements: list = []
    buf: list[Line] = []

    def flush() -> None:
        if not buf:
            return
        paras = build_paragraphs(buf, L_area, Rp)
        _harmonize_lists(paras)
        _align(paras, L_area, R_area, page_cx=page.width / 2)
        _spacing(paras)
        # a hanging/indent measured against the text column, not the page area
        for p in paras:
            if p.align == "left" and p.kind != "list":
                p.left_indent = max(0.0, p.body_left - L_area)
                if p.left_indent < 0.6 * p.size:
                    p.left_indent = 0.0
            if p.kind == "list":
                p.left_indent = max(0.0, p.body_left - L_area)
        elements.extend(paras)
        buf.clear()

    for _top, kind, obj in items:
        if kind == "line":
            buf.append(obj)  # type: ignore[arg-type]
        else:
            flush()
            t: Table = obj  # type: ignore[assignment]
            t.left_indent = max(0.0, t.bounds.x - L_area)
            elements.append(t)
    flush()

    # spacing around tables
    for i, el in enumerate(elements):
        if isinstance(el, Table):
            prev = elements[i - 1] if i > 0 else None
            if isinstance(prev, Para):
                gap = el.top - prev.bottom
                prev.space_after = min(max(0.0, gap - 2.0), 36.0)
        elif i > 0 and isinstance(elements[i - 1], Table):
            gap = el.top - elements[i - 1].bottom
            el.space_before = min(max(0.0, gap - 2.0), 36.0)
    if elements and isinstance(elements[0], Para):
        elements[0].space_before = 0.0
    return elements, (ml, mt, mr, mb)


def _outer_grids(grids: list[Grid]) -> list[Grid]:
    out = []
    for g in grids:
        inside = any(
            h is not g and h.bounds.x <= g.bounds.x + 1 and h.bounds.y <= g.bounds.y + 1
            and h.bounds.right >= g.bounds.right - 1 and h.bounds.bottom >= g.bounds.bottom - 1
            and h.bounds.w * h.bounds.h > g.bounds.w * g.bounds.h
            for h in grids
        )
        if not inside:
            out.append(g)
    return out


# --------------------------------------------------------------------------- document level

def _classify(pages: list[PageDoc]) -> None:
    free: list[Para] = [e for p in pages for e in p.elements if isinstance(e, Para)]
    pool = free or [q for p in pages for e in p.elements if isinstance(e, Table)
                    for c in e.cells for q in c.paras]
    if not pool:
        return
    weight: Counter = Counter()
    for q in pool:
        weight[round(q.size * 2) / 2] += len(q.text.strip())
    body = weight.most_common(1)[0][0]
    body_bold = sum(len(q.text) * q.bold_frac for q in pool) / max(1, sum(len(q.text) for q in pool))

    cands: list[Para] = []
    for p in free:
        if p.kind == "list" and not _HEAD_NUM_RE.match(p.text):
            continue
        t = p.text.strip()
        n = len(t)
        wide = sum(_wide(ch) for ch in t) * 2 >= max(1, n)
        if n == 0 or n > (40 if wide else 90) or p.n_lines > 2 or p.tabs:
            continue
        if t[-1] in "。.;；,，、":
            continue
        big = p.size >= body * 1.14
        boldish = p.bold_frac >= 0.8 and p.size >= body * 0.95 and body_bold < 0.4 and p.n_lines == 1
        if p.kind == "list":
            # a numbered line is a heading only when it also stands out
            if not (big or boldish):
                continue
        if big or boldish:
            p.kind = "heading"
            cands.append(p)
    sizes = sorted({round(p.size * 2) / 2 for p in cands if p.size >= body * 1.14}, reverse=True)
    merged: list[float] = []
    for s in sizes:
        if not merged or merged[-1] - s > 0.75:
            merged.append(s)

    def rank_of(p: Para) -> int:
        if p.size >= body * 1.14 and merged:
            s = round(p.size * 2) / 2
            return 1 + min(range(len(merged)), key=lambda i: abs(merged[i] - s))
        return len(merged) + 1

    def depth_of(p: Para) -> int | None:
        m = re.match(r"^(\d{1,2}(?:\.\d{1,2})*)", p.text.strip())
        return m.group(1).count(".") + 1 if m else None

    base: dict[int, int] = {}
    for p in cands:
        d = depth_of(p)
        if d is not None:
            r = rank_of(p)
            base[r] = min(base.get(r, d), d)
    for p in cands:
        r = rank_of(p)
        d = depth_of(p)
        p.level = max(1, min(4, r + (d - base[r] if d is not None else 0)))
        if p.marker:  # "1.1<TAB>Title" -> "1.1 Title"
            for run in p.runs:
                if "\t" in run.text:
                    run.text = run.text.replace("\t", p.marker_sep, 1)
                    break
            p.text = "".join(r_.text for r_ in p.runs)
        p.marker = None
        p.first_line = 0.0
        if p.align == "left":
            p.left_indent = max(0.0, p.first_left - p.area_left)
            if p.left_indent < 0.6 * p.size:
                p.left_indent = 0.0
        # centred / right-aligned headings keep the indents _align gave them


def structure_document(inputs: list[PageInput]) -> list[PageDoc]:
    docs: list[PageDoc] = []
    for pg in inputs:
        els, margins = _page_chunks(pg)
        docs.append(PageDoc(pg.page, pg.width, pg.height, margins, els))
    _classify(docs)
    return docs
