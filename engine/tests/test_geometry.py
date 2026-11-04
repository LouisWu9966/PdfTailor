import random

import pytest

from pdftailor_engine.geometry import PageGeometry, Rect


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
@pytest.mark.parametrize("crop", [None, (20, 30, 592, 762)])
def test_round_trip_and_stays_inside_crop(rot, crop):
    g = PageGeometry(1, (0, 0, 612, 792), crop, rot)
    rng = random.Random(7)
    box = g.crop
    for _ in range(300):
        dx, dy = rng.uniform(0, g.display_w), rng.uniform(0, g.display_h)
        ux, uy = g.display_to_user(dx, dy)
        assert box.left - 1e-9 <= ux <= box.right + 1e-9
        assert box.bottom - 1e-9 <= uy <= box.top + 1e-9
        bx, by = g.user_to_display(ux, uy)
        assert abs(bx - dx) < 1e-9 and abs(by - dy) < 1e-9


@pytest.mark.parametrize(
    "rot,expected",
    [(0, "top-left"), (90, "bottom-left"), (180, "bottom-right"), (270, "top-right")],
)
def test_display_origin_maps_to_expected_crop_corner(rot, expected):
    g = PageGeometry(1, (0, 0, 612, 792), None, rot)
    ux, uy = g.display_to_user(0, 0)
    corner = {
        "top-left": (g.crop.left, g.crop.top),
        "bottom-left": (g.crop.left, g.crop.bottom),
        "bottom-right": (g.crop.right, g.crop.bottom),
        "top-right": (g.crop.right, g.crop.top),
    }[expected]
    assert (round(ux, 6), round(uy, 6)) == corner


def test_display_size_swaps_for_quarter_turns():
    assert (PageGeometry(1, (0, 0, 612, 792), None, 0).display_w) == 612
    g = PageGeometry(1, (0, 0, 612, 792), None, 90)
    assert (g.display_w, g.display_h) == (792, 612)


def test_oversized_crop_is_clipped_to_media():
    g = PageGeometry(1, (0, 0, 612, 792), (-50, -50, 700, 900), 0)
    assert (g.crop.left, g.crop.bottom, g.crop.right, g.crop.top) == (0, 0, 612, 792)


def test_rect_helpers():
    a, b = Rect(10, 10, 20, 20), Rect(25, 25, 20, 20)
    assert a.intersect(b) == Rect(25, 25, 5, 5)
    assert a.intersect(Rect(100, 100, 5, 5)) is None
    assert a.union(b) == Rect(10, 10, 35, 35)
    u = PageGeometry(1, (0, 0, 612, 792), None, 90)
    back = u.user_rect_to_display(u.display_rect_to_user(Rect(10, 20, 100, 14)))
    assert all(abs(x - y) < 1e-9 for x, y in zip(back.to_list(), [10, 20, 100, 14]))
