"""Coordinate systems used throughout the engine.

Three spaces are involved in editing a PDF page:

* **display space** – what the user sees and clicks. Origin at the top-left of the
  visible page (CropBox, /Rotate applied), Y grows downward, unit = PDF point.
  Everything the shell sends or receives is in this space.
* **user space** – PDF's native space. Origin at the MediaBox bottom-left, Y grows
  upward. Content-stream operators and PDF writers live here.
* **pixels** – a render of the page at some scale; ``pixel = display * scale``.

Keeping the conversion in one place means page rotation and an offset crop box are
handled once, not at every call site.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle. Used for display space (top-left origin, Y down)."""

    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def inflate(self, dx: float, dy: float | None = None) -> "Rect":
        dy = dx if dy is None else dy
        return Rect(self.x - dx, self.y - dy, self.w + 2 * dx, self.h + 2 * dy)

    def union(self, other: "Rect") -> "Rect":
        x0, y0 = min(self.x, other.x), min(self.y, other.y)
        return Rect(x0, y0, max(self.right, other.right) - x0, max(self.bottom, other.bottom) - y0)

    def intersect(self, other: "Rect") -> "Rect | None":
        x0, y0 = max(self.x, other.x), max(self.y, other.y)
        x1, y1 = min(self.right, other.right), min(self.bottom, other.bottom)
        if x1 <= x0 or y1 <= y0:
            return None
        return Rect(x0, y0, x1 - x0, y1 - y0)

    def contains_point(self, px: float, py: float) -> bool:
        return self.x <= px <= self.right and self.y <= py <= self.bottom

    def to_list(self) -> list[float]:
        return [self.x, self.y, self.w, self.h]

    @staticmethod
    def from_bounds(left: float, top: float, right: float, bottom: float) -> "Rect":
        return Rect(min(left, right), min(top, bottom), abs(right - left), abs(bottom - top))

    @staticmethod
    def from_list(v: list[float] | tuple[float, ...]) -> "Rect":
        return Rect(float(v[0]), float(v[1]), float(v[2]), float(v[3]))


@dataclass(frozen=True)
class UserRect:
    """Rectangle in PDF user space (bottom-left origin, Y up)."""

    left: float
    bottom: float
    right: float
    top: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.top - self.bottom


def normalise_rotation(rotate: int) -> int:
    r = int(rotate) % 360
    return (r // 90) * 90


def _normalised(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    l, b, r, t = box
    return (min(l, r), min(b, t), max(l, r), max(b, t))


class PageGeometry:
    """Maps between display space and PDF user space for one page."""

    def __init__(self, page_number: int, media_box, crop_box, rotation: int):
        media = _normalised(tuple(media_box))
        crop = _normalised(tuple(crop_box)) if crop_box is not None else media
        # A CropBox is clipped to the MediaBox, which is what conforming viewers do.
        crop = (
            max(crop[0], media[0]),
            max(crop[1], media[1]),
            min(crop[2], media[2]),
            min(crop[3], media[3]),
        )
        if crop[2] - crop[0] <= 0 or crop[3] - crop[1] <= 0:
            crop = media

        self.page_number = page_number
        self.media = UserRect(*media)
        self.crop = UserRect(*crop)
        self.rotation = normalise_rotation(rotation)

        swap = self.rotation in (90, 270)
        self.display_w = self.crop.height if swap else self.crop.width
        self.display_h = self.crop.width if swap else self.crop.height

    # ---- points -------------------------------------------------------------------
    def display_to_user(self, dx: float, dy: float) -> tuple[float, float]:
        w, h = self.crop.width, self.crop.height
        r = self.rotation
        if r == 90:
            cx, cy = dy, dx
        elif r == 180:
            cx, cy = w - dx, dy
        elif r == 270:
            cx, cy = w - dy, h - dx
        else:
            cx, cy = dx, h - dy
        return self.crop.left + cx, self.crop.bottom + cy

    def user_to_display(self, ux: float, uy: float) -> tuple[float, float]:
        cx, cy = ux - self.crop.left, uy - self.crop.bottom
        w, h = self.crop.width, self.crop.height
        r = self.rotation
        if r == 90:
            return cy, cx
        if r == 180:
            return w - cx, cy
        if r == 270:
            return h - cy, w - cx
        return cx, h - cy

    # ---- rectangles ---------------------------------------------------------------
    def display_rect_to_user(self, rect: Rect) -> UserRect:
        pts = [
            self.display_to_user(rect.x, rect.y),
            self.display_to_user(rect.right, rect.y),
            self.display_to_user(rect.x, rect.bottom),
            self.display_to_user(rect.right, rect.bottom),
        ]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return UserRect(min(xs), min(ys), max(xs), max(ys))

    def user_rect_to_display(self, rect: UserRect) -> Rect:
        pts = [
            self.user_to_display(rect.left, rect.bottom),
            self.user_to_display(rect.right, rect.bottom),
            self.user_to_display(rect.left, rect.top),
            self.user_to_display(rect.right, rect.top),
        ]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    # ---- direction of display axes, expressed in user space ------------------------
    def baseline_matrix(self) -> tuple[float, float]:
        """Unit vector in user space that points along the display +X axis.

        Text drawn upright on the display runs along this vector, so a text matrix of
        ``[a b -b a e f]`` with ``(a, b)`` from here makes it read correctly on a rotated
        page without touching the page itself.
        """
        x0, y0 = self.display_to_user(0, 0)
        x1, y1 = self.display_to_user(1, 0)
        return x1 - x0, y1 - y0

    def to_json(self) -> dict:
        return {
            "page": self.page_number,
            "width": self.display_w,
            "height": self.display_h,
            "rotation": self.rotation,
            "media": [self.media.left, self.media.bottom, self.media.right, self.media.top],
            "crop": [self.crop.left, self.crop.bottom, self.crop.right, self.crop.top],
        }
