"""Symbol detection against rendered lines: built-in symbols are found, look-alike glyphs are not."""
import numpy as np
import pytest

from scangen import Style, degrade, render_line
from pdftailor_engine.analysis.line import scan_line

POSITIVE = [
    "■ 項目一　□ 項目二　● 項目三",
    "「引用文字」與『書名』",
    "□ 同意　■ 不同意　（請擇一勾選）",
    "▲ 上升　▼ 下降　◆ 重點　△ 注意",
    "■ Total: 42 units □ Pending 「Note」",
    "○ 選項甲　◇ 選項乙",
]
NEGATIVE = [
    "回國園團圓 國中日田目口人口",
    "Fast Jump Pro. Total: 42 units, r j F J P 4 H T",
    "發票號碼：AB-12345678　金額：NT$ 1,280.50",
    "Note: see Section 3.2; item (a), item [b] and {c}.",
    "本公司所有資料均依個資法處理，請放心。回覆日期：2026年11月3日",
]
SYMBOLS = set("■□●○▲△▼▽◆◇▪▫「」『』•")
SCAN = dict(blur=0.7, noise=6, skew_deg=0.3, jpeg=78)


def _found(text, style, **deg):
    img = render_line(text, style)
    if deg:
        img = degrade(img, seed=1, **deg)
    sc = scan_line(np.asarray(img), text_hint=text, em_hint=style.size_px)
    return [m.char for _, m in sc.symbols]


def _score(style, **deg):
    tp = fn = fp = neg_fp = 0
    for text in POSITIVE:
        expected = [c for c in text if c in SYMBOLS]
        got = _found(text, style, **deg)
        i = 0
        for g in got:
            if i < len(expected) and g == expected[i]:
                tp += 1
                i += 1
            else:
                fp += 1
        fn += len(expected) - i
    for text in NEGATIVE:
        neg_fp += len(_found(text, style, **deg))
    return tp, fn, fp, neg_fp


@pytest.mark.parametrize("family,weight", [("noto", "Regular"), ("noto", "Bold"), ("noto-serif", "Regular")])
@pytest.mark.parametrize("px", [36, 52])
def test_clean_lines_are_perfect(family, weight, px):
    tp, fn, fp, neg_fp = _score(Style(family=family, weight=weight, size_px=px))
    assert (fn, fp, neg_fp) == (0, 0, 0)
    assert tp == sum(1 for t in POSITIVE for c in t if c in SYMBOLS)


@pytest.mark.parametrize("family,weight", [("noto", "Regular"), ("noto-serif", "Regular")])
@pytest.mark.parametrize("px", [36, 52])
def test_scan_noise_keeps_symbols(family, weight, px):
    tp, fn, fp, neg_fp = _score(Style(family=family, weight=weight, size_px=px), **SCAN)
    assert neg_fp == 0
    assert fp == 0
    assert fn <= 1  # at most one symbol missed across the whole sample
