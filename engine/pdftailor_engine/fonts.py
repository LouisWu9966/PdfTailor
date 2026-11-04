"""Finding an installed font for a style, and getting it into reportlab.

Three jobs:

* **Index** the fonts installed on the machine (family names in every language the
  name table carries, weight, italic, width) and cache that index, because opening a
  few hundred Windows fonts takes seconds.
* **Resolve** a :class:`TextStyle` plus the text to draw into a primary face and, for
  characters that face lacks, fallback faces — the way Word draws CJK text set in
  Calibri with the East Asian font. Bold or italic that no face provides is reported
  as "synthesise", which the writer does with a stroke or a shear.
* **Embed**: reportlab only reads TrueType outlines, but many CJK fonts (Noto/Source
  Han, some .otf) are CFF. Each face is subset to the characters actually drawn and,
  if needed, converted to TrueType outlines before it is registered. Subsetting first
  keeps the conversion of a 65k-glyph CJK font to milliseconds.

Fonts are only read from the user's own machine; nothing is shipped with the engine.
Faces whose licence forbids embedding (OS/2 fsType bit 1) are skipped.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import logging
import threading
from dataclasses import asdict, dataclass, field

from fontTools import subset as ft_subset
from fontTools.ttLib import TTCollection, TTFont, newTable

from .fontnames import _key
from .models import TextStyle

# --------------------------------------------------------------------------------------
# defaults (first installed family wins; Windows names first, then Linux test fonts)
# --------------------------------------------------------------------------------------
CJK_SANS = ["Microsoft JhengHei", "Noto Sans CJK TC", "Noto Sans TC", "Source Han Sans TC", "PingFang TC", "Microsoft YaHei"]
CJK_SERIF = ["PMingLiU", "MingLiU", "Noto Serif CJK TC", "Noto Serif TC", "Source Han Serif TC", "SimSun"]
LATIN_SANS = ["Calibri", "Arial", "Liberation Sans", "DejaVu Sans"]
LATIN_SERIF = ["Times New Roman", "Cambria", "Liberation Serif", "DejaVu Serif"]
# Fonts that hold ■□●○▲△▼▽◆◇「」『』 and other symbols, tried when everything else lacks a char.
SYMBOL_FALLBACK = ["Segoe UI Symbol", "Microsoft JhengHei", "MS Gothic", "Noto Sans CJK TC", "DejaVu Sans", "Noto Sans Symbols2"]

SERIF_FAMILIES = {
    _key(f)
    for f in (
        "Times New Roman", "Cambria", "Georgia", "Garamond", "Book Antiqua", "Palatino Linotype", "Constantia",
        "Liberation Serif", "DejaVu Serif", "PMingLiU", "MingLiU", "Noto Serif CJK TC", "Noto Serif TC",
        "Source Han Serif TC", "SimSun", "NSimSun", "MS Mincho", "MS PMincho", "DFKai-SB", "BiauKai", "KaiTi",
    )
}

INDEX_VERSION = 2

logging.getLogger("fontTools").setLevel(logging.ERROR)  # odd head timestamps etc. are harmless


def is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x2E80 <= o <= 0x9FFF
        or 0xF900 <= o <= 0xFAFF
        or 0x3000 <= o <= 0x303F
        or 0xFF00 <= o <= 0xFFEF
        or 0x20000 <= o <= 0x2FA1F
    )


def default_font_dirs() -> list[str]:
    dirs: list[str] = []
    if sys.platform == "win32":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        dirs.append(os.path.join(windir, "Fonts"))
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    elif sys.platform == "darwin":
        dirs += ["/System/Library/Fonts", "/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    else:
        dirs += ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"), os.path.expanduser("~/.local/share/fonts")]
    return [d for d in dirs if os.path.isdir(d)]


def default_cache_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "PdfTailor", "fontindex.json")


# --------------------------------------------------------------------------------------
# index
# --------------------------------------------------------------------------------------
@dataclass
class FontFace:
    path: str
    index: int  # face number inside a .ttc, 0 otherwise
    family: str  # English typographic family (name ID 16, else 1)
    subfamily: str
    aliases: list[str]  # _key() of every family name in every language
    weight: int
    italic: bool
    width: int  # OS/2 usWidthClass, 5 = normal
    cff: bool
    embeddable: bool

    @property
    def id(self) -> str:
        return f"{self.path}#{self.index}"

    @property
    def bold(self) -> bool:
        return self.weight >= 600


def _names(font: TTFont) -> tuple[str, str, list[str]]:
    name = font["name"]
    fams: list[str] = []
    english_family = english_sub = ""
    for rec in name.names:
        if rec.nameID not in (1, 2, 16, 17, 21, 22):
            continue
        try:
            s = rec.toUnicode()
        except Exception:
            continue
        english = rec.platformID == 3 and rec.langID == 0x409 or rec.platformID == 1 and rec.langID == 0
        if rec.nameID in (1, 16, 21):
            fams.append(s)
            if english and (rec.nameID == 16 or not english_family):
                english_family = s
        elif english and (rec.nameID == 17 or not english_sub):
            english_sub = s
    if not english_family and fams:
        english_family = fams[0]
    return english_family, english_sub or "Regular", sorted({_key(f) for f in fams if f})


def _face_from(font: TTFont, path: str, index: int) -> FontFace | None:
    if "name" not in font or "cmap" not in font:
        return None
    family, sub, aliases = _names(font)
    if not family:
        return None
    os2 = font["OS/2"] if "OS/2" in font else None
    weight = int(getattr(os2, "usWeightClass", 400) or 400)
    sel = int(getattr(os2, "fsSelection", 0) or 0)
    mac = int(font["head"].macStyle) if "head" in font else 0
    low = sub.lower()
    italic = bool(sel & 1) or bool(mac & 2) or "italic" in low or "oblique" in low
    if weight < 600 and (sel & 32 or mac & 1) and "bold" in low:
        weight = 700
    fs_type = int(getattr(os2, "fsType", 0) or 0)
    return FontFace(
        path=path,
        index=index,
        family=family,
        subfamily=sub,
        aliases=aliases,
        weight=weight,
        italic=italic,
        width=int(getattr(os2, "usWidthClass", 5) or 5),
        cff="CFF " in font or "CFF2" in font,
        embeddable=not (fs_type & 0x0002) or bool(fs_type & 0x000C),
    )


def read_faces(path: str) -> list[FontFace]:
    faces: list[FontFace] = []
    try:
        if path.lower().endswith((".ttc", ".otc")):
            coll = TTCollection(path, lazy=True)
            for i, f in enumerate(coll.fonts):
                face = _face_from(f, path, i)
                if face:
                    faces.append(face)
            coll.close()
        else:
            with TTFont(path, lazy=True) as f:
                face = _face_from(f, path, 0)
                if face:
                    faces.append(face)
    except Exception:
        pass  # broken or unsupported files are simply not offered
    return faces


_EXTS = (".ttf", ".otf", ".ttc", ".otc")


class FontIndex:
    def __init__(self, faces: list[FontFace]):
        self.faces = faces
        self._by_alias: dict[str, list[FontFace]] = {}
        for f in faces:
            for a in f.aliases:
                self._by_alias.setdefault(a, []).append(f)
        self._coverage: dict[str, frozenset[int]] = {}
        self._lock = threading.Lock()

    # ---- construction ---------------------------------------------------------------
    @classmethod
    def scan(cls, dirs: list[str] | None = None, cache_path: str | None = "default") -> "FontIndex":
        dirs = default_font_dirs() if dirs is None else dirs
        if cache_path == "default":
            cache_path = default_cache_path()
        cache: dict[str, dict] = {}
        if cache_path and os.path.isfile(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as fh:
                    data = json.load(fh)
                if data.get("version") == INDEX_VERSION:
                    cache = data.get("files", {})
            except Exception:
                cache = {}

        files: dict[str, dict] = {}
        faces: list[FontFace] = []
        for d in dirs:
            for root, _subdirs, names in os.walk(d):
                for n in names:
                    if not n.lower().endswith(_EXTS):
                        continue
                    p = os.path.join(root, n)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    stamp = f"{st.st_size}:{int(st.st_mtime)}"
                    hit = cache.get(p)
                    if hit and hit.get("stamp") == stamp:
                        fs = [FontFace(**x) for x in hit["faces"]]
                    else:
                        fs = read_faces(p)
                    files[p] = {"stamp": stamp, "faces": [asdict(f) for f in fs]}
                    faces.extend(fs)
        if cache_path:
            try:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                tmp = cache_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump({"version": INDEX_VERSION, "files": files}, fh, ensure_ascii=False)
                os.replace(tmp, cache_path)
            except OSError:
                pass
        return cls(faces)

    # ---- queries --------------------------------------------------------------------
    def families(self) -> list[str]:
        """English family names, for the shell's font picker."""
        return sorted({f.family for f in self.faces if f.embeddable}, key=str.lower)

    def family_faces(self, family: str | None) -> list[FontFace]:
        if not family:
            return []
        return [f for f in self._by_alias.get(_key(family), []) if f.embeddable]

    def has_family(self, family: str) -> bool:
        return bool(self.family_faces(family))

    def coverage(self, face: FontFace) -> frozenset[int]:
        with self._lock:
            cov = self._coverage.get(face.id)
            if cov is None:
                try:
                    with TTFont(face.path, fontNumber=face.index, lazy=True) as f:
                        cov = frozenset((f.getBestCmap() or {}).keys())
                except Exception:
                    cov = frozenset()
                self._coverage[face.id] = cov
            return cov

    def covers(self, face: FontFace, ch: str) -> bool:
        return ch.isspace() or ord(ch) in self.coverage(face)

    def pick_face(self, family: str | None, bold: bool, italic: bool) -> FontFace | None:
        faces = self.family_faces(family)
        if not faces:
            return None
        target = 700 if bold else 400

        def score(f: FontFace) -> float:
            s = abs(f.weight - target) / 100.0
            s += 3.0 if f.italic != italic else 0.0
            s += 2.0 * abs(f.width - 5)
            return s

        return min(faces, key=score)

    def first_family(self, names: list[str]) -> str | None:
        for n in names:
            if self.has_family(n):
                return n
        return None

    # ---- resolving a style ----------------------------------------------------------
    def is_cjk_family(self, family: str) -> bool:
        face = self.pick_face(family, False, False)
        return face is not None and ord("中") in self.coverage(face)

    def resolve(self, style: TextStyle, text: str, serif: bool | None = None) -> "ResolvedFont":
        """Picks faces for drawing ``text`` in ``style``, the way Office does it.

        Office keeps two fonts per run: a Latin one and an East Asian one. CJK characters,
        and symbols whose width depends on the script (■ □ 「 」 ※ …) in a line that has
        CJK, go to the East Asian font; everything else to the Latin font. Characters
        neither has fall through a symbol fallback chain.

        ``serif`` is a hint for blocks whose family is unknown (scanned text); when None
        the requested family's own serif-ness decides, defaulting to sans.
        """
        if serif is None:
            serif = bool(style.family) and _key(style.family) in SERIF_FAMILIES
        has_cjk = any(is_cjk(c) for c in text)
        latin_default = self.first_family(LATIN_SERIF if serif else LATIN_SANS)
        cjk_default = self.first_family(CJK_SERIF if serif else CJK_SANS)

        notes: list[str] = []
        requested = None
        if style.family:
            if self.has_family(style.family):
                requested = style.family
            else:
                notes.append(f"font-missing:{style.family}")
        if requested and self.is_cjk_family(requested):
            # A CJK family asked for by name (標楷體, 微軟正黑體) keeps its own Latin glyphs:
            # Office PDFs name the Latin font separately when it differs.
            cjk_family = latin_family = requested
        else:
            latin_family = requested or latin_default or cjk_default
            cjk_family = cjk_default or latin_family
        if latin_family is None or cjk_family is None:
            raise FontError("no usable font installed")

        def chain(first: str, second: str) -> list[FontFace]:
            names: list[str] = []
            for fam in [first, second] + SYMBOL_FALLBACK:
                if fam and fam not in names and self.has_family(fam):
                    names.append(fam)
            faces = [self.pick_face(f, style.bold, style.italic) for f in names]
            return [f for f in faces if f is not None]

        latin_chain = chain(latin_family, cjk_family)
        cjk_chain = chain(cjk_family, latin_family)

        segments: list[Segment] = []
        missing: list[str] = []
        for ch in text:
            if ch.isspace() and segments:
                segments[-1].text += ch  # spaces never switch fonts
                continue
            east = is_cjk(ch) or (has_cjk and is_ambiguous(ch))
            faces = cjk_chain if east else latin_chain
            face = next((f for f in faces if self.covers(f, ch)), None)
            if face is None:
                missing.append(ch)
                face = faces[0]
            if segments and segments[-1].face.id == face.id:
                segments[-1].text += ch
            else:
                segments.append(Segment(ch, face, style.bold and not face.bold, style.italic and not face.italic))
        if missing:
            notes.append("chars-missing:" + "".join(sorted(set(missing))))
        return ResolvedFont(
            family=requested or (cjk_family if has_cjk else latin_family),
            segments=segments,
            missing="".join(sorted(set(missing))),
            notes=notes,
        )


def is_ambiguous(ch: str) -> bool:
    """Symbols Office draws with the East Asian font when they sit in CJK text."""
    o = ord(ch)
    return 0x2010 <= o <= 0x2BFF or o in (0x00A7, 0x00B0, 0x00B1, 0x00B7, 0x00D7, 0x00F7)


class FontError(Exception):
    pass


@dataclass
class Segment:
    text: str
    face: FontFace
    synth_bold: bool
    synth_italic: bool


@dataclass
class ResolvedFont:
    family: str
    segments: list[Segment]
    missing: str = ""
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------
# embedding for reportlab
# --------------------------------------------------------------------------------------
def _cff_to_glyf(font: TTFont, max_err: float = 0.6) -> None:
    """Replaces CFF outlines with quadratic TrueType outlines, in place."""
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    order = font.getGlyphOrder()
    glyph_set = font.getGlyphSet()
    glyf = newTable("glyf")
    glyf.glyphOrder = order
    glyf.glyphs = {}
    for name in order:
        pen = TTGlyphPen(glyph_set)
        glyph_set[name].draw(Cu2QuPen(pen, max_err, reverse_direction=True))
        glyf.glyphs[name] = pen.glyph()
    font["glyf"] = glyf
    font["loca"] = newTable("loca")
    for tag in ("CFF ", "CFF2", "VORG"):
        if tag in font:
            del font[tag]
    maxp = font["maxp"]
    maxp.tableVersion = 0x00010000
    for attr in ("maxZones", "maxTwilightPoints", "maxStorage", "maxFunctionDefs", "maxInstructionDefs",
                 "maxStackElements", "maxSizeOfInstructions", "maxComponentElements"):
        setattr(maxp, attr, 0)
    maxp.maxZones = 1
    post = font["post"]
    post.formatType = 2.0
    post.extraNames = []
    post.mapping = {}
    post.glyphOrder = order
    font["head"].glyphDataFormat = 0
    font.sfntVersion = "\x00\x01\x00\x00"


class Embedder:
    """Registers subset faces with reportlab and hands out their names.

    One instance per export: a face drawn on several pages is subset once with every
    character it needs, so call :meth:`want` for all text first, then :meth:`name`.
    """

    def __init__(self, workdir: str | None = None):
        self.workdir = workdir or tempfile.mkdtemp(prefix="pdftailor-fonts-")
        self._chars: dict[str, set[str]] = {}
        self._faces: dict[str, FontFace] = {}
        self._names: dict[str, str] = {}
        self._paths: dict[str, str] = {}
        self._tt: dict[str, TTFont] = {}
        self._bounds: dict[tuple[str, str], tuple[float, float, float, float, float]] = {}

    def want(self, face: FontFace, text: str) -> None:
        self._faces[face.id] = face
        have = self._chars.setdefault(face.id, set())
        new = {c for c in text if not c.isspace() or c == " "} - have
        if new:
            have.update(new)
            self._names.pop(face.id, None)  # a later name() builds a bigger subset
            self._bounds = {k: v for k, v in self._bounds.items() if k[0] != face.id}

    def name(self, face: FontFace) -> str:
        if face.id in self._names:
            return self._names[face.id]
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont as RLTTFont

        chars = self._chars.get(face.id, set()) | {" "}
        digest = hashlib.sha1((face.id + "".join(sorted(chars))).encode("utf-8")).hexdigest()[:12]
        out = os.path.join(self.workdir, f"{digest}.ttf")
        if not os.path.isfile(out):
            font = TTFont(face.path, fontNumber=face.index)
            opts = ft_subset.Options()
            opts.name_IDs = ["*"]
            opts.name_languages = ["*"]
            opts.notdef_outline = True
            opts.layout_features = []  # reportlab draws by cmap; no shaping
            opts.hinting = False
            opts.desubroutinize = True
            sub = ft_subset.Subsetter(opts)
            sub.populate(unicodes=[ord(c) for c in chars])
            sub.subset(font)
            if "CFF " in font or "CFF2" in font:
                _cff_to_glyf(font)
            font.save(out)
            font.close()
        rl_name = f"PT_{digest}"
        if rl_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(RLTTFont(rl_name, out))
        self._names[face.id] = rl_name
        self._paths[rl_name] = out
        return rl_name

    def glyph_bounds(self, face: FontFace, ch: str) -> tuple[float, float, float, float, float]:
        """``(xmin, ymin, xmax, ymax, advance)`` of one character, in ems (y up).

        Used to place text by its ink rather than its em box: OCR and pdfium both report
        ink bounds, and an ideograph's ink sits ~0.12 em below the baseline while a capital
        Latin letter's sits on it.
        """
        from fontTools.pens.boundsPen import BoundsPen

        key = (face.id, ch)
        hit = self._bounds.get(key)
        if hit is not None:
            return hit
        rl_name = self.name(face)
        tt = self._tt.get(rl_name)
        if tt is None:
            tt = TTFont(self._paths[rl_name], lazy=False)
            self._tt[rl_name] = tt
        upem = tt["head"].unitsPerEm
        gname = (tt.getBestCmap() or {}).get(ord(ch))
        if gname is None:
            res = (0.0, 0.0, 0.0, 0.0, 0.0)
        else:
            gs = tt.getGlyphSet()
            pen = BoundsPen(gs)
            gs[gname].draw(pen)
            adv = tt["hmtx"][gname][0] / upem
            if pen.bounds is None:
                res = (0.0, 0.0, 0.0, 0.0, adv)
            else:
                x0, y0, x1, y1 = pen.bounds
                res = (x0 / upem, y0 / upem, x1 / upem, y1 / upem, adv)
        self._bounds[key] = res
        return res

    def width(self, face: FontFace, text: str, size: float) -> float:
        from reportlab.pdfbase import pdfmetrics

        return pdfmetrics.stringWidth(text, self.name(face), size)


_DEFAULT_INDEX: FontIndex | None = None
_DEFAULT_LOCK = threading.Lock()


def default_index() -> FontIndex:
    global _DEFAULT_INDEX
    with _DEFAULT_LOCK:
        if _DEFAULT_INDEX is None:
            _DEFAULT_INDEX = FontIndex.scan()
        return _DEFAULT_INDEX
