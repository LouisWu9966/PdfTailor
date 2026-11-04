"""Reading PDF font names, and decoding symbolic bullet fonts.

PDF font names are a mess: ``ABCDEF+Calibri-Bold``, ``TimesNewRomanPS-BoldItalicMT``,
``DFKaiShu-SB-Estd-BF`` (that is 標楷體), ``Arial,Italic``. This module turns them into
a family that exists on a real machine plus bold/italic flags, so an edited line can be
redrawn in the same face rather than in whatever happens to be handy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --------------------------------------------------------------------------------------
# canonical families
# --------------------------------------------------------------------------------------
# Keys are the font name lower-cased with everything but letters and digits removed.
# Metric-compatible substitutes are listed on purpose: a PDF that says Helvetica was
# almost certainly laid out to Arial's metrics, which is what Windows has.
_ALIASES: dict[str, str] = {
    # Latin, Office
    "calibri": "Calibri",
    "calibrilight": "Calibri Light",
    "cambria": "Cambria",
    "cambriamath": "Cambria Math",
    "candara": "Candara",
    "consolas": "Consolas",
    "constantia": "Constantia",
    "corbel": "Corbel",
    "arial": "Arial",
    "arialmt": "Arial",
    "arialnarrow": "Arial Narrow",
    "arialblack": "Arial Black",
    "helvetica": "Arial",
    "helveticaneue": "Arial",
    "times": "Times New Roman",
    "timesnewroman": "Times New Roman",
    "timesnewromanps": "Times New Roman",
    "timesnewromanpsmt": "Times New Roman",
    "courier": "Courier New",
    "couriernew": "Courier New",
    "couriernewps": "Courier New",
    "couriernewpsmt": "Courier New",
    "verdana": "Verdana",
    "tahoma": "Tahoma",
    "georgia": "Georgia",
    "trebuchetms": "Trebuchet MS",
    "segoeui": "Segoe UI",
    "segoeuisymbol": "Segoe UI Symbol",
    "segoeuiemoji": "Segoe UI Emoji",
    "garamond": "Garamond",
    "bookantiqua": "Book Antiqua",
    "palatinolinotype": "Palatino Linotype",
    "centurygothic": "Century Gothic",
    "impact": "Impact",
    "comicsansms": "Comic Sans MS",
    "lucidaconsole": "Lucida Console",
    # Traditional Chinese
    "microsoftjhenghei": "Microsoft JhengHei",
    "microsoftjhengheiui": "Microsoft JhengHei UI",
    "msjh": "Microsoft JhengHei",
    "msjhbd": "Microsoft JhengHei",
    "pmingliu": "PMingLiU",
    "pmingliuextb": "PMingLiU-ExtB",
    "mingliu": "MingLiU",
    "mingliuextb": "MingLiU-ExtB",
    "dfkaishusbestdbf": "DFKai-SB",
    "dfkaisb": "DFKai-SB",
    "dfkaishu": "DFKai-SB",
    "kaiu": "DFKai-SB",
    "biaukai": "BiauKai",
    "dflihei": "Microsoft JhengHei",
    "notosanstc": "Noto Sans TC",
    "notosanscjktc": "Noto Sans CJK TC",
    "notoseriftc": "Noto Serif TC",
    "notoserifcjktc": "Noto Serif CJK TC",
    "sourcehansanstc": "Source Han Sans TC",
    "sourcehanseriftc": "Source Han Serif TC",
    "pingfangtc": "PingFang TC",
    "heititc": "Heiti TC",
    "stheitilight": "STHeiti",
    "lihei": "LiHei Pro",
    # Simplified Chinese
    "simsun": "SimSun",
    "nsimsun": "NSimSun",
    "simhei": "SimHei",
    "simkai": "KaiTi",
    "kaiti": "KaiTi",
    "fangsong": "FangSong",
    "microsoftyahei": "Microsoft YaHei",
    "microsoftyaheiui": "Microsoft YaHei UI",
    "dengxian": "DengXian",
    "deng": "DengXian",
    "notosanssc": "Noto Sans SC",
    "pingfangsc": "PingFang SC",
    # Japanese / Korean (common in Office PDFs)
    "msgothic": "MS Gothic",
    "mspgothic": "MS PGothic",
    "msmincho": "MS Mincho",
    "mspmincho": "MS PMincho",
    "meiryo": "Meiryo",
    "yugothic": "Yu Gothic",
    "malgungothic": "Malgun Gothic",
    # symbolic
    "symbol": "Symbol",
    "symbolmt": "Symbol",
    "wingdings": "Wingdings",
    "wingdings2": "Wingdings 2",
    "wingdings3": "Wingdings 3",
    "zapfdingbats": "ZapfDingbats",
    "webdings": "Webdings",
}

_STYLE_BOLD = ("bold", "black", "heavy", "semibold", "demibold", "demi", "extrabold", "ultrabold", "bd")
_STYLE_ITALIC = ("italic", "oblique", "ital", "kursiv")
# Tokens that describe weight/width/format but not the family.
_NOISE = ("regular", "roman", "normal", "book", "std", "pro", "mt", "ps", "psmt", "bf", "estd")


@dataclass(frozen=True)
class ParsedFont:
    family: str | None  # canonical family when recognised
    hint: str  # best-effort readable family when not recognised
    bold: bool
    italic: bool
    symbolic: bool  # Symbol / Wingdings / Dingbats: codes are not Unicode


_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _split_camel(s: str) -> str:
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    s = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_pdf_font_name(raw: str | None) -> ParsedFont:
    name = (raw or "").strip()
    name = _SUBSET_PREFIX.sub("", name)
    if not name:
        return ParsedFont(None, "", False, False, False)

    # "Arial,Bold" and "Arial-BoldMT" both put the style after the first separator.
    sep = re.split(r"[,\-]", name, maxsplit=1)
    base = sep[0]
    style_part = sep[1] if len(sep) > 1 else ""
    # Some producers glue the style on without a separator ("ArialBold", "CalibriBold").
    glued = name.replace("-", "").replace(",", "")
    low = (style_part or "").lower()
    low_all = glued.lower()

    bold = any(t in low for t in _STYLE_BOLD) or bool(re.search(r"(bold|black|heavy)$", low_all))
    italic = any(t in low for t in _STYLE_ITALIC) or bool(re.search(r"(italic|oblique)$", low_all))
    # Base-14 style names: "Helvetica-BoldOblique", "Times-BoldItalic".
    if re.search(r"bold", low_all) and not bold:
        bold = True
    if re.search(r"(italic|oblique)", low_all) and not italic:
        italic = True

    family_key = _key(base)
    # Strip glued style words so "arialbold" -> "arial".
    for token in ("bolditalic", "boldoblique", "bold", "italic", "oblique", "regular"):
        if family_key.endswith(token) and len(family_key) > len(token):
            family_key = family_key[: -len(token)]
    for token in _NOISE:
        if family_key.endswith(token) and len(family_key) > len(token) + 2:
            trimmed = family_key[: -len(token)]
            if trimmed in _ALIASES or family_key not in _ALIASES:
                family_key = trimmed

    family = _ALIASES.get(family_key)
    if family is None:
        # "ArialMT" style leftovers: try dropping a trailing MT/PS once more.
        for token in ("mt", "ps"):
            if family_key.endswith(token) and family_key[: -len(token)] in _ALIASES:
                family = _ALIASES[family_key[: -len(token)]]
                break

    symbolic = (family or "") in {"Symbol", "Wingdings", "Wingdings 2", "Wingdings 3", "ZapfDingbats", "Webdings"}
    return ParsedFont(family, family or _split_camel(base), bold, italic, symbolic)


# --------------------------------------------------------------------------------------
# symbolic fonts
# --------------------------------------------------------------------------------------
# Word writes bullets with the Symbol and Wingdings fonts. Depending on the producer the
# extracted code is the raw byte, or the byte plus U+F000 (private use). Both are mapped.
# These tables hold only the entries that Office actually emits for bullets and checks;
# they are deliberately small and extendable from a user JSON file (see fonts.py).
_SYMBOL: dict[int, str] = {
    0xB7: "•",  # •
    0xAE: "→",  # →
    0xAC: "←",  # ←
    0xAD: "↑",  # ↑
    0xAF: "↓",  # ↓
    0xA7: "♣",  # ♣
    0xA8: "♦",  # ♦
    0xA9: "♥",  # ♥
    0xAA: "♠",  # ♠
    0xB1: "±",  # ±
    0xB0: "°",  # °
}

_WINGDINGS: dict[int, str] = {
    0x6C: "●",  # l  ●
    0x6D: "❍",  # m  ❍
    0x6E: "■",  # n  ■
    0x6F: "□",  # o  □
    0x71: "❑",  # q  ❑
    0x72: "❒",  # r  ❒
    0x75: "◆",  # u  ◆
    0x76: "❖",  # v  ❖
    0xA7: "▪",  # ▪
    0xA8: "◻",  # ◻
    0xD8: "➢",  # ➢
    0xFB: "✗",  # ✗
    0xFC: "✓",  # ✓
    0xFE: "☑",  # ☑
}


def decode_symbolic_char(ch: str, family: str | None) -> str:
    """Maps a code from a symbolic font to the Unicode character it displays.

    Characters that are not from a symbolic font are returned untouched, and so are
    codes the tables do not know — a private-use code is better kept (and reported)
    than replaced with a guess.
    """
    if not ch:
        return ch
    code = ord(ch)
    byte = code - 0xF000 if 0xF020 <= code <= 0xF0FF else code
    if family in ("Symbol",) and byte <= 0xFF:
        return _SYMBOL.get(byte, ch)
    if family in ("Wingdings",) and byte <= 0xFF:
        return _WINGDINGS.get(byte, ch)
    if 0xF020 <= code <= 0xF0FF:
        # Private-use code from a font we could not identify: Wingdings is by far the
        # most common source of these in Office documents.
        return _WINGDINGS.get(byte, _SYMBOL.get(byte, ch))
    return ch


def is_private_use(ch: str) -> bool:
    return bool(ch) and (0xE000 <= ord(ch) <= 0xF8FF)
