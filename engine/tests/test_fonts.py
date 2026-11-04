"""Font index, Office-style script splitting, embedding (CFF -> TrueType) and font matching."""
import numpy as np
import pytest

from scangen import Style, degrade, render_line
from pdftailor_engine.analysis.imaging import ink_mask
from pdftailor_engine.fontmatch import match_font
from pdftailor_engine.fonts import Embedder, FontIndex
from pdftailor_engine.models import TextStyle


@pytest.fixture(scope="module")
def fonts(tmp_path_factory):
    return FontIndex.scan(cache_path=str(tmp_path_factory.mktemp("fi") / "fi.json"))


def test_index_has_cjk_and_latin(fonts):
    if not (fonts.has_family("Noto Sans CJK TC") and fonts.has_family("DejaVu Sans")):
        pytest.skip("test fonts (Noto Sans CJK TC, DejaVu Sans) are not installed on this machine")
    assert fonts.has_family("Noto Sans CJK TC")
    assert fonts.has_family("DejaVu Sans")
    assert fonts.is_cjk_family("Noto Sans CJK TC")
    assert not fonts.is_cjk_family("DejaVu Sans")


def test_cache_round_trip(tmp_path):
    p = str(tmp_path / "fi.json")
    a = FontIndex.scan(cache_path=p)
    b = FontIndex.scan(cache_path=p)
    assert {f.id for f in a.faces} == {f.id for f in b.faces}


def test_office_script_split(fonts):
    r = fonts.resolve(TextStyle(), "本公司資料 ■□「引用」 Total 42")
    fams = [(s.text.strip(), s.face.family) for s in r.segments]
    assert fams[0][1] == "Noto Sans CJK TC" and "■" in fams[0][0] and "「" in fams[0][0]
    assert fams[-1][0] == "Total 42" and not fonts.is_cjk_family(fams[-1][1])


def test_missing_family_falls_back_and_notes(fonts):
    r = fonts.resolve(TextStyle(family="Calibri", bold=True), "Total 金額")
    assert "font-missing:Calibri" in r.notes
    assert all(s.face.bold for s in r.segments)  # real bold faces, nothing synthesised
    assert not any(s.synth_bold for s in r.segments)


def test_embed_cff_cjk_and_measure(fonts):
    emb = Embedder()
    r = fonts.resolve(TextStyle(), "季度營運報告")
    face = r.segments[0].face
    assert face.cff  # Noto CJK is CFF: exercises the conversion
    emb.want(face, "季度營運報告")
    assert emb.width(face, "季度營運報告", 10) == pytest.approx(60, abs=0.5)
    x0, y0, x1, y1, adv = emb.glyph_bounds(face, "營")
    assert y0 < 0 < y1 and adv == pytest.approx(1.0, abs=0.01)


def test_embed_grows_subset_when_more_text_is_wanted(fonts):
    emb = Embedder()
    face = fonts.pick_face("DejaVu Sans", False, False)
    emb.want(face, "abc")
    n1 = emb.name(face)
    emb.want(face, "xyz")
    n2 = emb.name(face)
    assert n1 != n2 and emb.glyph_bounds(face, "x")[4] > 0


@pytest.mark.parametrize("family,truth,texts", [
    ("noto", "Noto Sans CJK TC", ["本公司所有資料均依個資法處理請放心", "請於收到通知後七個工作天內回覆"]),
    ("noto-serif", "Noto Serif CJK TC", ["本公司所有資料均依個資法處理請放心", "品質檢驗結果合格並已簽核存查"]),
    ("dejavu", "DejaVu Sans", ["Quarterly report revenue grew steadily", "Total amount due within thirty days"]),
    ("dejavu-serif", "DejaVu Serif", ["Quarterly report revenue grew steadily", "Please review the attached specification"]),
])
@pytest.mark.parametrize("weight", ["Regular", "Bold"])
def test_font_match_on_degraded_scan(fonts, family, truth, texts, weight):
    for i, t in enumerate(texts):
        px = 34
        img = np.asarray(degrade(render_line(t, Style(family=family, weight=weight, size_px=px)), seed=i, blur=0.7, noise=6, jpeg=78, skew_deg=0))
        mask, _, _ = ink_mask(img)
        g = match_font(mask, t, weight == "Bold", False, fonts, scale=1.0)
        assert g is not None and g.family == truth, (t, g and g.ranking[:3])
        assert abs(g.size / px - 1) < 0.06
