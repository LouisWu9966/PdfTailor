"""Tesseract (Apache-2.0) driven through its command line.

The command line, not a binding, on purpose: no native library has to be linked into
the engine, the user can drop in a better ``chi_tra`` model, and a bundled
``tesseract.exe`` works the same as an installed one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

from .base import OcrEngine, OcrLine, OcrUnavailable

_WIN_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def find_tesseract() -> str | None:
    """Looks for tesseract: env var, next to the engine, PATH, usual install folders."""
    env = os.environ.get("PDFTAILOR_TESSERACT")
    if env and os.path.isfile(env):
        return env
    here = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, "frozen", False) else __file__))
    for root in (here, os.path.dirname(here), os.path.dirname(os.path.dirname(here))):
        for sub in ("tesseract", "Tesseract-OCR", "."):
            for exe in ("tesseract.exe", "tesseract"):
                p = os.path.join(root, sub, exe)
                if os.path.isfile(p):
                    return p
    found = shutil.which("tesseract")
    if found:
        return found
    for p in _WIN_PATHS:
        if os.path.isfile(p):
            return p
    return None


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return 0x2E80 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF


def join_words(words: list[str]) -> str:
    """Joins Tesseract words back into a line.

    Tesseract puts a space between neighbouring Chinese characters ("發 票"), which is an
    artefact. A space is kept only between two Latin-ish neighbours.
    """
    out = ""
    for w in words:
        if not w:
            continue
        if out:
            a, b = out[-1], w[0]
            if not (_is_cjk(a) or _is_cjk(b)):
                out += " "
        out += w
    return out


class TesseractOcr(OcrEngine):
    name = "tesseract"
    can_recrop = True

    def __init__(self, languages: str = "chi_tra+eng", exe: str | None = None, tessdata: str | None = None):
        self.exe = exe or find_tesseract()
        if not self.exe:
            raise OcrUnavailable(
                "找不到 Tesseract。請安裝 Tesseract OCR 並含 chi_tra 語言資料，"
                "或把 tesseract 資料夾放在引擎旁邊（也可用環境變數 PDFTAILOR_TESSERACT 指定）。"
            )
        bundled = os.path.join(os.path.dirname(self.exe), "tessdata")
        # A copied Tesseract folder must use its own language data, not whatever an
        # unrelated install put in TESSDATA_PREFIX.
        self.tessdata = tessdata or (bundled if os.path.isdir(bundled) else os.environ.get("TESSDATA_PREFIX"))
        self.languages = self._usable_languages(languages)

    # ---- plumbing ------------------------------------------------------------------
    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["OMP_THREAD_LIMIT"] = "1"  # callers parallelise across crops themselves
        if self.tessdata:
            env["TESSDATA_PREFIX"] = self.tessdata
        return env

    def _run(self, args: list[str], timeout: float = 120) -> str:
        proc = subprocess.run(
            [self.exe, *args],
            capture_output=True,
            timeout=timeout,
            env=self._env(),
            creationflags=_NO_WINDOW,
        )
        if proc.returncode != 0 and not proc.stdout:
            raise OcrUnavailable(proc.stderr.decode("utf-8", "replace").strip() or "Tesseract 執行失敗")
        return proc.stdout.decode("utf-8", "replace")

    def installed_languages(self) -> set[str]:
        try:
            out = self._run(["--list-langs"], timeout=30)
        except Exception:
            return set()
        return {l.strip() for l in out.splitlines()[1:] if l.strip()}

    def _usable_languages(self, wanted: str) -> str:
        have = self.installed_languages()
        picked = [l for l in wanted.split("+") if l in have]
        if not picked:
            if "eng" in have:
                picked = ["eng"]
            else:
                raise OcrUnavailable(
                    "Tesseract 沒有可用的語言資料。請安裝 chi_tra（繁體中文）與 eng 的 traineddata。"
                )
        return "+".join(picked)

    @property
    def has_chinese(self) -> bool:
        return "chi_tra" in self.languages or "chi_sim" in self.languages

    @staticmethod
    def _save(rgb: np.ndarray, path: str) -> None:
        Image.fromarray(rgb).save(path, format="PNG")

    # ---- engine API ----------------------------------------------------------------
    def recognize_page(self, rgb: np.ndarray, psm: int = 3) -> list[OcrLine]:
        with tempfile.TemporaryDirectory(prefix="pt-ocr-") as tmp:
            img = os.path.join(tmp, "p.png")
            self._save(rgb, img)
            tsv = self._run([img, "stdout", "-l", self.languages, "--psm", str(psm), "--dpi", str(int(round(self.dpi))), "tsv"])
        return self._parse_lines(tsv)

    def recognize_crop(self, rgb: np.ndarray) -> tuple[str, float]:
        with tempfile.TemporaryDirectory(prefix="pt-ocr-") as tmp:
            img = os.path.join(tmp, "c.png")
            self._save(rgb, img)
            tsv = self._run([img, "stdout", "-l", self.languages, "--psm", "7", "--dpi", str(int(round(self.dpi))), "tsv"])
        lines = self._parse_lines(tsv)
        if not lines:
            return "", -1.0
        text = join_words([l.text for l in lines]) if len(lines) > 1 else lines[0].text
        confs = [l.confidence for l in lines if l.confidence >= 0]
        return text, (sum(confs) / len(confs) if confs else -1.0)

    @staticmethod
    def _parse_lines(tsv: str) -> list[OcrLine]:
        words: dict[tuple[int, int, int], list[tuple[str, float, int, int, int, int]]] = {}
        for raw in tsv.splitlines()[1:]:
            parts = raw.split("\t", 11)
            if len(parts) < 12 or parts[0] != "5":
                continue
            text = parts[11].strip()
            if not text:
                continue
            try:
                key = (int(parts[2]), int(parts[3]), int(parts[4]))
                left, top, width, height = (int(parts[i]) for i in (6, 7, 8, 9))
                conf = float(parts[10])
            except ValueError:
                continue
            words.setdefault(key, []).append((text, conf, left, top, width, height))

        lines: list[OcrLine] = []
        for key in sorted(words):
            ws = sorted(words[key], key=lambda w: w[2])
            text = join_words([w[0] for w in ws])
            if not text:
                continue
            x0 = min(w[2] for w in ws)
            y0 = min(w[3] for w in ws)
            x1 = max(w[2] + w[4] for w in ws)
            y1 = max(w[3] + w[5] for w in ws)
            confs = [w[1] for w in ws if w[1] >= 0]
            lines.append(OcrLine(text, x0, y0, x1 - x0, y1 - y0, sum(confs) / len(confs) if confs else -1.0))
        return lines
