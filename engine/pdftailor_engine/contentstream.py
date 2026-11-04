"""A byte-level PDF content-stream scanner and a text redactor built on it.

Editing happens by byte range, never by decoding and re-encoding: a CJK show-text
operand is binary CID codes that would not survive a trip through a text string.
Only the string operand of a matching show-text operator is emptied; every other byte is
left exactly as it was.

The redactor does not need glyph widths. ``Td``/``TD``/``T*`` move relative to the text
*line* matrix and ``Tm`` is absolute, so removing a run does not shift what follows.
The cost is that a run is removed whole — callers must redact whole lines.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum

_WS = b"\x00\t\n\x0c\r "
_DELIM = b"()<>[]{}/%"


class T(Enum):
    NUMBER = 1
    NAME = 2
    LSTRING = 3
    HSTRING = 4
    OPERATOR = 5
    ARR_OPEN = 6
    ARR_CLOSE = 7
    DICT_OPEN = 8
    DICT_CLOSE = 9
    BRACE_OPEN = 10
    BRACE_CLOSE = 11
    COMMENT = 12
    INLINE_IMAGE = 13


@dataclass(frozen=True)
class Token:
    kind: T
    start: int
    end: int
    text: str | None = None

    @property
    def is_string(self) -> bool:
        return self.kind in (T.LSTRING, T.HSTRING)

    @property
    def is_operand(self) -> bool:
        return self.kind in (T.NUMBER, T.NAME, T.LSTRING, T.HSTRING, T.ARR_OPEN, T.ARR_CLOSE, T.DICT_OPEN, T.DICT_CLOSE)

    @property
    def number(self) -> float:
        try:
            return float(self.text or "0")
        except ValueError:
            return 0.0


_NUMBER = re.compile(rb"^[+-]?(\d+\.?\d*|\.\d+)$")


def _is_number(word: bytes) -> bool:
    return bool(_NUMBER.match(word))


def tokenize(buf: bytes) -> list[Token]:
    """Splits a content stream. Every non-whitespace byte belongs to exactly one token."""
    out: list[Token] = []
    i, n = 0, len(buf)
    while i < n:
        c = buf[i]
        if c in _WS:
            i += 1
            continue
        if c == 0x25:  # %
            j = i
            while j < n and buf[j] not in b"\r\n":
                j += 1
            out.append(Token(T.COMMENT, i, j))
            i = j
            continue
        if c == 0x28:  # (
            j, depth = i + 1, 1
            while j < n and depth:
                if buf[j] == 0x5C:  # backslash escapes the next byte
                    j += 2
                    continue
                if buf[j] == 0x28:
                    depth += 1
                elif buf[j] == 0x29:
                    depth -= 1
                j += 1
            j = min(j, n)
            out.append(Token(T.LSTRING, i, j))
            i = j
            continue
        if c == 0x3C:  # <
            if i + 1 < n and buf[i + 1] == 0x3C:
                out.append(Token(T.DICT_OPEN, i, i + 2))
                i += 2
                continue
            j = buf.find(b">", i)
            j = n if j < 0 else j + 1
            out.append(Token(T.HSTRING, i, j))
            i = j
            continue
        if c == 0x3E:  # >
            if i + 1 < n and buf[i + 1] == 0x3E:
                out.append(Token(T.DICT_CLOSE, i, i + 2))
                i += 2
            else:
                i += 1  # stray, malformed
            continue
        if c == 0x5B:
            out.append(Token(T.ARR_OPEN, i, i + 1))
            i += 1
            continue
        if c == 0x5D:
            out.append(Token(T.ARR_CLOSE, i, i + 1))
            i += 1
            continue
        if c == 0x7B:
            out.append(Token(T.BRACE_OPEN, i, i + 1))
            i += 1
            continue
        if c == 0x7D:
            out.append(Token(T.BRACE_CLOSE, i, i + 1))
            i += 1
            continue
        if c == 0x2F:  # /
            j = i + 1
            while j < n and buf[j] not in _WS and buf[j] not in _DELIM:
                j += 1
            out.append(Token(T.NAME, i, j, buf[i + 1 : j].decode("latin-1")))
            i = j
            continue

        j = i
        while j < n and buf[j] not in _WS and buf[j] not in _DELIM:
            j += 1
        if j == i:
            j = i + 1
        word = buf[i:j]
        if _is_number(word):
            out.append(Token(T.NUMBER, i, j, word.decode("latin-1")))
        elif word == b"BI":
            end = _skip_inline_image(buf, j)
            out.append(Token(T.INLINE_IMAGE, i, end, "BI"))
            j = end
        else:
            out.append(Token(T.OPERATOR, i, j, word.decode("latin-1")))
        i = j
    return out


def _read_declared_length(buf: bytes, start: int, end: int) -> int:
    for m in re.finditer(rb"/(L|Length)\s+(\d+)", buf[start:end]):
        return int(m.group(2))
    return 0


def _skip_inline_image(buf: bytes, after_bi: int) -> int:
    """Returns the index just past the ``EI`` that closes an inline image.

    The payload is arbitrary binary and can contain bytes that look like operators or
    even ``EI``, so the dictionary's ``/L`` length is trusted when present and the
    "whitespace before, delimiter after" rule is the fallback.
    """
    n = len(buf)
    i = after_bi
    id_end = -1
    while i < n - 1:
        if buf[i] == 0x49 and buf[i + 1] == 0x44 and (i == 0 or buf[i - 1] in _WS or buf[i - 1] in _DELIM):
            id_end = i + 2
            break
        i += 1
    if id_end < 0:
        return n
    declared = _read_declared_length(buf, after_bi, id_end - 2)
    data_start = id_end + 1 if id_end < n and buf[id_end] in _WS else id_end
    if declared > 0 and data_start + declared <= n:
        probe = data_start + declared
        while probe < n and buf[probe] in _WS:
            probe += 1
        if buf[probe : probe + 2] == b"EI":
            return probe + 2
    scan = data_start
    while scan < n - 1:
        if buf[scan : scan + 2] == b"EI" and scan > data_start and buf[scan - 1] in _WS:
            nxt = scan + 2
            if nxt >= n or buf[nxt] in _WS or buf[nxt] in _DELIM:
                return nxt
        scan += 1
    return n


# --------------------------------------------------------------------------------------
# matrices
# --------------------------------------------------------------------------------------
Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mmul(m1: Matrix, m2: Matrix) -> Matrix:
    """``m1`` applied first, then ``m2``."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


# --------------------------------------------------------------------------------------
# redaction
# --------------------------------------------------------------------------------------
# Regions are (left, bottom, right, top) in user space of the stream being scanned.
Region = tuple[float, float, float, float]

_SHOW = {"Tj", "TJ", "'", '"'}


@dataclass
class RedactionResult:
    content: bytes
    removed_runs: int
    # (name, ctm) for each ``Do`` met, so a caller can recurse into form XObjects.
    xobject_calls: list[tuple[str, Matrix]]

    @property
    def changed(self) -> bool:
        return self.removed_runs > 0


@dataclass
class _TextState:
    size: float = 0.0
    hscale: float = 1.0
    rise: float = 0.0
    leading: float = 0.0

    def clone(self) -> "_TextState":
        return _TextState(self.size, self.hscale, self.rise, self.leading)


def _inside(regions: list[Region], x: float, y: float, height: float) -> bool:
    # The probe is the run's origin: left end of the baseline. A region taken from tight
    # glyph boxes contains it, so only a little slack is needed. The slack must stay well
    # under the leading, or the previous line's baseline would be caught as well.
    pad = max(0.8, 0.18 * height)
    for l, b, r, t in regions:
        if l - pad <= x <= r + pad and b - pad <= y <= t + pad:
            return True
    return False


def redact_stream(content: bytes, regions: list[Region], base_ctm: Matrix = IDENTITY) -> RedactionResult:
    if not content or not regions:
        return RedactionResult(content, 0, [])

    tokens = tokenize(content)
    spans: list[tuple[int, int, bytes]] = []
    xcalls: list[tuple[str, Matrix]] = []

    ctm = base_ctm
    tm = tlm = IDENTITY
    st = _TextState()
    stack: list[tuple[Matrix, _TextState]] = []
    operands: list[Token] = []
    removed = 0

    for tok in tokens:
        if tok.is_operand:
            operands.append(tok)
            continue
        if tok.kind in (T.COMMENT, T.INLINE_IMAGE, T.BRACE_OPEN, T.BRACE_CLOSE):
            continue
        if tok.kind is not T.OPERATOR:
            continue

        op = tok.text
        if op == "q":
            stack.append((ctm, st.clone()))
        elif op == "Q":
            if stack:
                ctm, st = stack.pop()
        elif op == "cm" and len(operands) >= 6:
            ctm = mmul(tuple(t.number for t in operands[-6:]), ctm)  # type: ignore[arg-type]
        elif op == "BT":
            tm = tlm = IDENTITY
        elif op == "Tf" and len(operands) >= 2:
            st.size = operands[-1].number
        elif op == "Tz" and operands:
            st.hscale = operands[-1].number / 100.0
        elif op == "Ts" and operands:
            st.rise = operands[-1].number
        elif op == "TL" and operands:
            st.leading = operands[-1].number
        elif op == "Tm" and len(operands) >= 6:
            tm = tlm = tuple(t.number for t in operands[-6:])  # type: ignore[assignment]
        elif op in ("Td", "TD") and len(operands) >= 2:
            tx, ty = operands[-2].number, operands[-1].number
            if op == "TD":
                st.leading = -ty
            tlm = mmul((1, 0, 0, 1, tx, ty), tlm)
            tm = tlm
        elif op == "T*":
            tlm = mmul((1, 0, 0, 1, 0, -st.leading), tlm)
            tm = tlm
        elif op == "Do" and operands and operands[-1].kind is T.NAME:
            xcalls.append((operands[-1].text or "", ctm))
        elif op in _SHOW:
            if op in ("'", '"'):
                tlm = mmul((1, 0, 0, 1, 0, -st.leading), tlm)
                tm = tlm
            scale = (st.size * st.hscale, 0.0, 0.0, st.size, 0.0, st.rise)
            render = mmul(mmul(scale, tm), ctm)
            height = math.hypot(render[2], render[3]) or abs(st.size)
            if _inside(regions, render[4], render[5], height):
                hit = False
                for s in operands:
                    if s.is_string:
                        spans.append((s.start, s.end, b"()" if s.kind is T.LSTRING else b"<>"))
                        hit = True
                if hit:
                    removed += 1
        operands = []

    if not spans:
        return RedactionResult(content, 0, xcalls)

    spans.sort()
    out = bytearray()
    last = 0
    for s, e, rep in spans:
        if s < last:
            continue
        out += content[last:s]
        out += rep
        last = e
    out += content[last:]
    return RedactionResult(bytes(out), removed, xcalls)
