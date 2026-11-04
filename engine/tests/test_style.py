"""Bold / italic / size estimation on rendered lines with scanner-like degradation."""
import random

import numpy as np
import pytest

from scangen import Style, degrade, render_line
from pdftailor_engine.analysis.line import scan_line
from pdftailor_engine.analysis.style import estimate_styles

CJK = ["發票號碼與金額統計報表項目", "本公司所有資料均依個資法處理請放心", "請於收到通知後七個工作天內回覆",
       "採購單編號及供應商名稱確認事項", "品質檢驗結果合格並已簽核存查", "出貨日期與交貨地點依合約規定"]
LAT = ["Quarterly report revenue grew steadily", "Margins stayed flat across the region",
       "Please review the attached specification", "Shipment confirmed for the end of the month",
       "Total amount due within thirty days"]
CFG = dict(blur=0.7, noise=6, skew_deg=0.0, jpeg=78)


def _page(fam_cjk, fam_lat, px, seed):
    rng = random.Random(seed)
    lines = []  # text, style, truth_bold, truth_italic
    for t in rng.sample(CJK, 4):
        lines.append((t, Style(family=fam_cjk, weight="Regular", size_px=px), False, False))
    for t in rng.sample(LAT, 3):
        lines.append((t, Style(family=fam_lat, weight="Regular", size_px=px), False, False))
    lines.append((rng.choice(CJK), Style(family=fam_cjk, weight="Bold", size_px=px), True, False))
    lines.append((rng.choice(LAT), Style(family=fam_lat, weight="Bold", size_px=px), True, False))
    lines.append((rng.choice(LAT), Style(family=fam_lat, weight="Regular", size_px=px, shear=0.21), False, True))
    return lines


def _run():
    r = dict(bold_tp=0, bold_fn=0, bold_fp=0, it_tp=0, it_fn=0, it_fp=0, n=0)
    size_err = []
    for fam_cjk, fam_lat in [("noto", "dejavu"), ("noto-serif", "dejavu-serif")]:
        for px in (30, 40, 50):
            for seed in range(3):
                lines = _page(fam_cjk, fam_lat, px, seed)
                scans = [scan_line(np.asarray(degrade(render_line(t, s), seed=seed * 7 + i, **CFG)),
                                   text_hint=t, em_hint=px) for i, (t, s, _, _) in enumerate(lines)]
                for (t, s, tb, ti), e in zip(lines, estimate_styles(scans)):
                    r["n"] += 1
                    r["bold_tp" if tb and e.bold else "bold_fn" if tb else "bold_fp" if e.bold else "n"] += 0 if not (tb or e.bold) else 1
                    r["it_tp" if ti and e.italic else "it_fn" if ti else "it_fp" if e.italic else "n"] += 0 if not (ti or e.italic) else 1
                    size_err.append(abs(e.size_px / px - 1) * 100)
    return r, np.array(size_err)


@pytest.fixture(scope="module")
def result():
    return _run()


def test_bold_recall_and_precision(result):
    r, _ = result
    total_bold = r["bold_tp"] + r["bold_fn"]
    assert r["bold_tp"] / total_bold >= 0.85
    assert r["bold_fp"] <= 0.03 * r["n"]


def test_italic_recall_and_precision(result):
    r, _ = result
    total = r["it_tp"] + r["it_fn"]
    assert r["it_tp"] / total >= 0.80
    assert r["it_fp"] <= 0.03 * r["n"]


def test_size_error_is_small(result):
    _, err = result
    assert np.median(err) < 4.0
    assert np.percentile(err, 90) < 12.0
