"""PDFs with real structure (paragraphs, lists, ruled and borderless tables) for the Word export.

Fonts are embedded TrueType: reportlab's built-in Helvetica is not embedded and pdfium then
measures glyph gaps from a substitute font, which splits digits ("1 2") — not what real
documents do.
"""

from __future__ import annotations

import os

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

LIB = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
LIBB = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
WQY = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"

HAVE_FONTS = all(os.path.exists(p) for p in (LIB, LIBB, WQY))
if HAVE_FONTS:
    pdfmetrics.registerFont(TTFont("Lib", LIB))
    pdfmetrics.registerFont(TTFont("LibB", LIBB))
    pdfmetrics.registerFont(TTFont("CJK", WQY, subfontIndex=0))

W, H = 595.0, 842.0
L, R = 72.0, W - 72.0


def wrap(text: str, font: str, size: float, width: float, first_width: float | None = None) -> list[str]:
    lines: list[str] = []
    cur = ""
    cjk = any(ord(ch) > 0x2E80 for ch in text)
    tokens = list(text) if cjk else text.split()
    for tok in tokens:
        t = cur + tok if cjk else (cur + " " + tok).strip()
        lim = first_width if (first_width is not None and not lines) else width
        if stringWidth(t, font, size) > lim and cur:
            lines.append(cur)
            cur = tok
        else:
            cur = t
    lines.append(cur)
    return lines


def paragraph(c, y, text, font="Lib", size=10.5, lead=14.0, left=L, right=R, indent=0.0) -> float:
    lines = wrap(text, font, size, right - left, right - left - indent)
    c.setFont(font, size)
    for i, ln in enumerate(lines):
        c.drawString(left + (indent if i == 0 else 0), y, ln)
        y -= lead
    return y


def grid(c, xs, ys, merges=(), fills=(), lw=0.8):
    """Ruled table. ``merges``: (r0, c0, r1, c1) inclusive; ``fills``: (r, c, (r,g,b))."""
    inside = set()
    for r0, c0, r1, c1 in merges:
        for r in range(r0, r1 + 1):
            for cc in range(c0, c1 + 1):
                inside.add((r, cc))

    def same_merge(a, b):
        return any(m[0] <= a[0] <= m[2] and m[1] <= a[1] <= m[3] and m[0] <= b[0] <= m[2] and m[1] <= b[1] <= m[3]
                   for m in merges)

    for r, cc, col in fills:
        c.setFillColorRGB(*col)
        c.rect(xs[cc], ys[r + 1], xs[cc + 1] - xs[cc], ys[r] - ys[r + 1], stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    c.setLineWidth(lw)
    rows, cols = len(ys) - 1, len(xs) - 1
    for r in range(rows + 1):
        for cc in range(cols):
            if 0 < r < rows and same_merge((r - 1, cc), (r, cc)):
                continue
            c.line(xs[cc], ys[r], xs[cc + 1], ys[r])
    for cc in range(cols + 1):
        for r in range(rows):
            if 0 < cc < cols and same_merge((r, cc - 1), (r, cc)):
                continue
            c.line(xs[cc], ys[r], xs[cc], ys[r + 1])


def report_pdf(path: str) -> str:
    """One A4 page: centred title, right-tab line, headings, wrapped paragraphs, bullets,
    numbered list, borderless table."""
    c = canvas.Canvas(path, pagesize=(W, H))
    y = H - 70
    c.setFont("LibB", 18)
    c.drawCentredString(W / 2, y, "Quarterly Quality Report")
    y -= 22
    c.setFont("Lib", 10)
    c.drawString(L, y, "Prepared by: QA Department")
    c.drawRightString(R, y, "2026-10-31")
    y -= 30
    c.setFont("LibB", 13)
    c.drawString(L, y, "1. Summary")
    y -= 20
    y = paragraph(c, y, "The third quarter saw a significant improvement in first-pass yield across all production lines, "
                  "driven mainly by the new inspection procedure introduced in July. Defect rates fell steadily month "
                  "over month and customer complaints dropped by almost a third compared with the previous quarter.")
    y -= 10
    y = paragraph(c, y, "Several issues remain open. The supplier audit for the plastic housings is overdue, and the "
                  "calibration records for two measuring instruments could not be located during the review.", indent=20)
    y -= 14
    c.setFont("LibB", 13)
    c.drawString(L, y, "2. Actions")
    y -= 20
    for t in ["Complete the supplier audit before the end of November and report the findings to the quality board.",
              "Re-calibrate the two instruments.", "Review the sampling plan."]:
        c.setFont("Lib", 10.5)
        c.drawString(L, y, "•")
        y = paragraph(c, y - 0, t, left=L + 16)
        y -= 3
    y -= 8
    c.setFont("LibB", 13)
    c.drawString(L, y, "3. Results")
    y -= 22
    cols = [L, L + 130, L + 250, L + 380]
    rows = [("Line", "Yield %", "Defects", "Notes"), ("A1", "98.2", "12", "Stable"),
            ("B2", "95.7", "31", "Tooling issue under review"), ("C3", "97.4", "18", "Improved")]
    for i, r in enumerate(rows):
        c.setFont("LibB" if i == 0 else "Lib", 10.5)
        for j, t in enumerate(r):
            if j in (1, 2):
                c.drawRightString(cols[j] + 70, y, t)
            else:
                c.drawString(cols[j], y, t)
        y -= 16
    y -= 14
    paragraph(c, y, "Figures are preliminary and will be confirmed once the month-end close is complete.")
    c.showPage()
    c.save()
    return path


def numbered_pdf(path: str) -> str:
    c = canvas.Canvas(path, pagesize=(W, H))
    y = H - 80
    c.setFont("LibB", 14)
    c.drawString(L, y, "Procedure")
    y -= 26
    for i, t in enumerate(["Open the valve slowly until the gauge reads two bar and hold it there for at least a minute.",
                           "Record the reading.", "Close the valve and sign the log sheet."], 1):
        c.setFont("Lib", 10.5)
        c.drawString(L, y, f"{i}.")
        y = paragraph(c, y, t, left=L + 22)
        y -= 3
    c.showPage()
    c.save()
    return path


def ruled_pdf(path: str, pages: int = 1) -> str:
    """Ruled table with a header band, a row-spanning and a column-spanning cell, wrapped
    cell text, a right-aligned number column; then text below the table."""
    c = canvas.Canvas(path, pagesize=(W, H))
    for pg in range(pages):
        y = H - 70
        c.setFont("LibB", 14)
        c.drawString(L, y, f"Inspection plan {pg + 1}")
        y -= 20
        top = y
        xs = [L, L + 80, L + 230, L + 330, R]
        ys = [top, top - 22, top - 62, top - 84, top - 106, top - 128]
        grid(c, xs, ys, merges=[(2, 0, 3, 0), (4, 1, 4, 2)], fills=[(0, 0, (0.8, 0.85, 1)), (0, 1, (0.8, 0.85, 1)),
                                                                     (0, 2, (0.8, 0.85, 1)), (0, 3, (0.8, 0.85, 1))])
        c.setFont("LibB", 10)
        for j, t in enumerate(["Item", "Check", "Limit", "Result"]):
            c.drawString(xs[j] + 5, ys[0] - 15, t)
        c.setFont("Lib", 10)
        c.drawString(xs[0] + 5, ys[1] - 15, "Surface")
        c.drawString(xs[1] + 5, ys[1] - 15, "No scratches, dents or")
        c.drawString(xs[1] + 5, ys[1] - 28, "discolouration visible")
        c.drawString(xs[2] + 5, ys[1] - 15, "Visual")
        c.drawRightString(xs[4] - 5, ys[1] - 15, "12.50")
        c.drawString(xs[0] + 5, ys[2] - 14, "Size")
        c.drawString(xs[1] + 5, ys[2] - 15, "Length")
        c.drawString(xs[2] + 5, ys[2] - 15, "±0.1 mm")
        c.drawRightString(xs[4] - 5, ys[2] - 15, "100.05")
        c.drawString(xs[1] + 5, ys[3] - 15, "Width")
        c.drawString(xs[2] + 5, ys[3] - 15, "±0.1 mm")
        c.drawRightString(xs[4] - 5, ys[3] - 15, "49.98")
        c.drawString(xs[1] + 5, ys[4] - 15, "Approved by the quality manager")
        y = ys[-1] - 28
        c.setFont("Lib", 10.5)
        paragraph(c, y, "Sign the record after the final check and file it with the batch documents for at least three years.")
        c.showPage()
    c.save()
    return path


def cjk_pdf(path: str) -> str:
    c = canvas.Canvas(path, pagesize=(W, H))
    y = H - 70
    c.setFont("CJK", 20)
    c.drawCentredString(W / 2, y, "品質管理程序書")
    y -= 36
    c.setFont("CJK", 14)
    c.drawString(L, y, "一、目的")
    y -= 24
    y = paragraph(c, y, "本程序規範公司內所有產品之進料檢驗、製程檢驗與出貨檢驗之作業方式，確保產品符合客戶要求及相關法規之規定，並提供各部門執行時之依據。",
                  font="CJK", size=10.5, lead=16, indent=21)
    y -= 6
    y = paragraph(c, y, "各部門應依本程序執行並保存相關紀錄，保存期限不得少於三年，逾期銷毀前須經管理代表核准。", font="CJK", size=10.5, lead=16, indent=21)
    y -= 14
    c.setFont("CJK", 14)
    c.drawString(L, y, "二、權責")
    y -= 24
    for t in ["（一）品保部負責制定檢驗標準並監督執行情形，每季提出檢討報告。", "（二）生產部負責製程自主檢查。"]:
        y = paragraph(c, y, t, font="CJK", size=10.5, lead=16)
        y -= 2
    c.showPage()
    c.save()
    return path


def band_pdf(path: str, dark: bool) -> str:
    """Header band made of filled cells with white gaps (no rules there); rules only below."""
    c = canvas.Canvas(path, pagesize=(W, H))
    y = H - 100
    xs = [L, L + 100, L + 250, R]
    ys = [y, y - 24, y - 48, y - 72, y - 96]
    for j in range(3):
        c.setFillColorRGB(*((0.1, 0.25, 0.5) if dark else (0.85, 0.85, 0.85)))
        c.rect(xs[j] + 0.75, ys[1] + 0.75, xs[j + 1] - xs[j] - 1.5, ys[0] - ys[1] - 1.5, stroke=0, fill=1)
    c.setFillColorRGB(*((1, 1, 1) if dark else (0, 0, 0)))
    c.setFont("LibB", 10)
    for j, t in enumerate(["Item", "Check", "Limit"]):
        c.drawString(xs[j] + 4, ys[0] - 15, t)
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.6)
    for r in range(1, 5):
        c.line(xs[0], ys[r], xs[3], ys[r])
    for x in xs:
        c.line(x, ys[1], x, ys[4])
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Lib", 10)
    for r in range(1, 4):
        for j in range(3):
            c.drawString(xs[j] + 4, ys[r] - 15, f"cell {r}{j}")
    c.showPage()
    c.save()
    return path
