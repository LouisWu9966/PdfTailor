"""Pixel-level primitives: background, ink mask, connected components, ink colour."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class Component:
    x: int
    y: int
    w: int
    h: int
    area: int
    label: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


def estimate_background(rgb: np.ndarray) -> tuple[RGB, float]:
    """The dominant colour of a crop and how much of the crop it covers.

    The crop of a text line is mostly paper, so the most common quantised colour is the
    background. The covered fraction doubles as a reliability measure: a flat page
    gives 0.85+, a textured or gradient background gives much less.
    """
    flat = rgb.reshape(-1, 3).astype(np.int32)
    if flat.size == 0:
        return (255, 255, 255), 0.0
    q = (flat // 16)
    key = q[:, 0] * 256 + q[:, 1] * 16 + q[:, 2]
    counts = np.bincount(key)
    top = int(counts.argmax())
    sel = key == top
    bg = np.median(flat[sel], axis=0)
    # Count everything close to that colour, not just its own bin: JPEG noise straddles
    # bin edges.
    near = np.linalg.norm(flat - bg, axis=1) < 28
    return (int(bg[0]), int(bg[1]), int(bg[2])), float(near.mean())


def distance_map(rgb: np.ndarray, bg: RGB) -> np.ndarray:
    diff = rgb.astype(np.float32) - np.array(bg, dtype=np.float32)
    return np.sqrt((diff * diff).sum(axis=2))


def ink_mask(rgb: np.ndarray, bg: RGB | None = None, min_threshold: float = 40.0) -> tuple[np.ndarray, RGB, float]:
    """Boolean mask of ink pixels, found by distance from the background colour.

    Distance (rather than darkness) makes light text on a dark band work the same way.
    Otsu picks the cut; a floor stops a blank crop from "finding" noise.
    """
    if bg is None:
        bg, _ = estimate_background(rgb)
    dist = distance_map(rgb, bg)
    d8 = np.clip(dist, 0, 255).astype(np.uint8)
    otsu, _ = cv2.threshold(d8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thr = max(float(otsu), min_threshold)
    return dist > thr, bg, thr


def find_components(mask: np.ndarray, min_area: int = 3) -> tuple[list[Component], np.ndarray]:
    u8 = mask.astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(u8, connectivity=8)
    comps = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area >= min_area:
            comps.append(Component(x, y, w, h, area, i))
    comps.sort(key=lambda c: (c.x, c.y))
    return comps, labels


def component_mask(labels: np.ndarray, comp: Component) -> np.ndarray:
    return labels[comp.y : comp.bottom, comp.x : comp.right] == comp.label


def ink_color(rgb: np.ndarray, mask: np.ndarray, bg: RGB) -> RGB:
    """Colour of the stroke cores: the pixels farthest from the background."""
    if not mask.any():
        return (17, 17, 17)
    dist = distance_map(rgb, bg)
    d = dist[mask]
    cut = np.percentile(d, 75)
    core = mask & (dist >= cut)
    if not core.any():
        core = mask
    c = np.median(rgb[core], axis=0)
    ink = (int(c[0]), int(c[1]), int(c[2]))
    # A region that is nearly the background colour has no real ink to report.
    if float(np.linalg.norm(np.array(ink) - np.array(bg))) < 40:
        return (17, 17, 17)
    return ink


def luminance(c: RGB) -> float:
    return (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255.0
