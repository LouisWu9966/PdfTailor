"""Data model shared by analysis, the writer and the JSON protocol."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from .geometry import Rect

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class TextStyle:
    """How a run of text looks.

    ``family`` is a canonical family name (``"Calibri"``, ``"Microsoft JhengHei"``) or
    ``None`` when nothing is known — the font resolver then picks a default from the
    script of the text. ``source`` records where the values came from so the UI can
    show how much to trust them: ``"font"`` (read from the PDF), ``"measured"``
    (estimated from pixels) or ``"user"``.
    """

    family: str | None = None
    size: float = 10.5
    bold: bool = False
    italic: bool = False
    color: RGB = (17, 17, 17)
    source: str = "default"
    bold_confidence: float = 1.0
    italic_confidence: float = 1.0

    def to_json(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "size": round(self.size, 2),
            "bold": self.bold,
            "italic": self.italic,
            "color": list(self.color),
            "source": self.source,
            "boldConfidence": round(self.bold_confidence, 3),
            "italicConfidence": round(self.italic_confidence, 3),
        }

    @staticmethod
    def from_json(d: dict[str, Any] | None) -> "TextStyle":
        d = d or {}
        c = d.get("color") or [17, 17, 17]
        return TextStyle(
            family=d.get("family"),
            size=float(d.get("size", 10.5)),
            bold=bool(d.get("bold", False)),
            italic=bool(d.get("italic", False)),
            color=(int(c[0]), int(c[1]), int(c[2])),
            source=str(d.get("source", "user")),
            bold_confidence=float(d.get("boldConfidence", 1.0)),
            italic_confidence=float(d.get("italicConfidence", 1.0)),
        )

    def with_(self, **kw) -> "TextStyle":
        return replace(self, **kw)

    def same_look(self, other: "TextStyle", size_tol: float = 0.35) -> bool:
        return (
            (self.family or "") == (other.family or "")
            and self.bold == other.bold
            and self.italic == other.italic
            and self.color == other.color
            and abs(self.size - other.size) <= size_tol
        )


@dataclass
class Run:
    """A stretch of characters that share one style."""

    text: str
    style: TextStyle
    x0: float = 0.0
    x1: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {"text": self.text, "style": self.style.to_json(), "x0": round(self.x0, 2), "x1": round(self.x1, 2)}

    @staticmethod
    def from_json(d: dict[str, Any]) -> "Run":
        return Run(d.get("text", ""), TextStyle.from_json(d.get("style")), float(d.get("x0", 0)), float(d.get("x1", 0)))


@dataclass
class Block:
    """One editable line of text on a page, in display space."""

    id: int
    page: int
    text: str
    bounds: Rect
    source: str  # "text" (real glyphs) or "ocr" (pixels)
    style: TextStyle
    runs: list[Run] = field(default_factory=list)
    background: RGB = (255, 255, 255)
    background_reliable: bool = True
    confidence: float | None = None
    # indices into ``text`` of characters that were not read by OCR but recovered by the
    # geometric symbol detector; lets the UI mark them as "detected shape".
    detected_symbols: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "page": self.page,
            "text": self.text,
            "bounds": [round(v, 2) for v in self.bounds.to_list()],
            "source": self.source,
            "style": self.style.to_json(),
            "runs": [r.to_json() for r in self.runs],
            "background": list(self.background),
            "backgroundReliable": self.background_reliable,
            "confidence": None if self.confidence is None else round(self.confidence, 3),
            "detectedSymbols": self.detected_symbols,
            "notes": self.notes,
        }


@dataclass
class Edit:
    """A block as the shell sends it back for export."""

    id: int
    page: int
    text: str
    original_text: str
    bounds: Rect
    original_bounds: Rect
    source: str
    style: TextStyle
    runs: list[Run]
    style_dirty: bool
    fill_mode: str  # "auto" | "solid" | "none"
    fill_color: RGB
    background_reliable: bool
    align: str  # "left" | "center" | "right"

    @property
    def modified(self) -> bool:
        return self.text != self.original_text or self.bounds != self.original_bounds or self.style_dirty

    @staticmethod
    def from_json(d: dict[str, Any]) -> "Edit":
        fc = d.get("fillColor") or d.get("background") or [255, 255, 255]
        return Edit(
            id=int(d["id"]),
            page=int(d["page"]),
            text=str(d.get("text", "")),
            original_text=str(d.get("originalText", d.get("text", ""))),
            bounds=Rect.from_list(d["bounds"]),
            original_bounds=Rect.from_list(d.get("originalBounds", d["bounds"])),
            source=str(d.get("source", "ocr")),
            style=TextStyle.from_json(d.get("style")),
            runs=[Run.from_json(r) for r in d.get("runs", [])],
            style_dirty=bool(d.get("styleDirty", False)),
            fill_mode=str(d.get("fillMode", "auto")),
            fill_color=(int(fc[0]), int(fc[1]), int(fc[2])),
            background_reliable=bool(d.get("backgroundReliable", True)),
            align=str(d.get("align", "left")),
        )

    @staticmethod
    def from_block(block: Block, **changes: Any) -> "Edit":
        """An unmodified edit of ``block``; keyword arguments change fields (``text=...``)."""
        e = Edit(
            id=block.id,
            page=block.page,
            text=block.text,
            original_text=block.text,
            bounds=block.bounds,
            original_bounds=block.bounds,
            source=block.source,
            style=block.style,
            runs=list(block.runs),
            style_dirty=False,
            fill_mode="auto",
            fill_color=block.background,
            background_reliable=block.background_reliable,
            align="left",
        )
        for k, v in changes.items():
            setattr(e, k, v)
        if "style" in changes:
            e.style_dirty = True
        return e
