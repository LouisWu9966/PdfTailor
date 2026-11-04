"""JSON Lines server: the shell talks to the engine over stdin/stdout.

Protocol (UTF-8, one JSON object per line):

* request   ``{"id": 7, "cmd": "analyze", "args": {"page": 1}}``
* response  ``{"id": 7, "ok": true, "result": {...}}``
            ``{"id": 7, "ok": false, "error": {"code": "user", "message": "..."}}``
* event     ``{"event": "progress", "id": 7, "stage": "ocr", "progress": 0.4}``
            ``{"event": "ready", "version": "...", "tesseract": true}`` once at start-up

Requests run one at a time in arrival order; ``{"id": 8, "cmd": "cancel", "args":
{"target": 7}}`` is handled immediately by the reader and makes request 7 stop at its
next progress point with error code ``cancelled``. stdout carries nothing but the
protocol; logs go to stderr.

Error codes: ``user`` (show the message), ``cancelled``, ``bad-request``, ``internal``.
"""

from __future__ import annotations

import io
import json
import queue
import sys
import threading
import traceback
from typing import Any

from . import __version__
from .ocr.tesseract import find_tesseract
from .pdfio import PdfOpenError
from .service import Cancelled, EngineError, Session


class Server:
    def __init__(self, inp: io.TextIOBase, out: io.TextIOBase, session: Session | None = None):
        self.inp = inp
        self.out = out
        self.session = session or Session()
        self._wlock = threading.Lock()
        self._queue: "queue.Queue[dict | None]" = queue.Queue()
        self._cancelled: set[Any] = set()
        self._running = True

    # ---- io -------------------------------------------------------------------------
    def send(self, obj: dict) -> None:
        line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        with self._wlock:
            self.out.write(line + "\n")
            self.out.flush()

    def _reader(self) -> None:
        for raw in self.inp:
            raw = raw.strip()
            if not raw:
                continue
            try:
                req = json.loads(raw)
                if not isinstance(req, dict):
                    raise ValueError("not an object")
            except ValueError as ex:
                self.send({"id": None, "ok": False, "error": {"code": "bad-request", "message": f"無法解析的請求：{ex}"}})
                continue
            if req.get("cmd") == "cancel":
                target = (req.get("args") or {}).get("target")
                self._cancelled.add(target)
                self.send({"id": req.get("id"), "ok": True, "result": {"cancelled": target}})
                continue
            self._queue.put(req)
            if req.get("cmd") == "shutdown":
                break
        self._queue.put(None)  # stdin closed: finish queued work, then exit

    # ---- dispatch -------------------------------------------------------------------
    def handle(self, req: dict) -> None:
        rid = req.get("id")
        cmd = req.get("cmd")
        args = req.get("args") or {}

        def progress(stage: str, f: float) -> bool:
            self.send({"event": "progress", "id": rid, "stage": stage, "progress": round(float(f), 3)})
            return rid not in self._cancelled

        s = self.session
        try:
            if cmd == "ping":
                result: Any = {"pong": True}
            elif cmd == "version":
                result = {"version": __version__, "tesseract": find_tesseract() is not None}
            elif cmd == "open":
                result = s.open(args["path"])
            elif cmd == "render":
                result = s.render_page(int(args["page"]), float(args.get("dpi", 110)), args.get("path"))
            elif cmd == "analyze":
                result = s.analyze(
                    int(args["page"]),
                    ocr=str(args.get("ocr", "auto")),
                    ocr_lines=args.get("ocrLines"),
                    force_ocr=bool(args.get("forceOcr", False)),
                    progress=progress,
                )
            elif cmd == "preview":
                result = s.preview(args.get("edits", []), args.get("pages"), float(args.get("dpi", 110)))
            elif cmd == "export":
                result = s.export(args.get("edits", []), args["out"], bool(args.get("verify", True)))
            elif cmd == "docx":
                result = s.export_docx(
                    args["out"],
                    pages=args.get("pages"),
                    edits=args.get("edits"),
                    ocr=str(args.get("ocr", "auto")),
                    progress=progress,
                )
            elif cmd == "fonts":
                result = s.fonts_list()
            elif cmd == "close":
                s.close()
                result = {"closed": True}
            elif cmd == "shutdown":
                self._running = False
                result = {"bye": True}
            else:
                self.send({"id": rid, "ok": False, "error": {"code": "bad-request", "message": f"未知指令：{cmd}"}})
                return
            self.send({"id": rid, "ok": True, "result": result})
        except Cancelled:
            self.send({"id": rid, "ok": False, "error": {"code": "cancelled", "message": "已取消"}})
        except (EngineError, PdfOpenError) as ex:
            self.send({"id": rid, "ok": False, "error": {"code": "user", "message": str(ex)}})
        except KeyError as ex:
            self.send({"id": rid, "ok": False, "error": {"code": "bad-request", "message": f"缺少參數：{ex}"}})
        except Exception as ex:  # keep serving after a bug in one request
            traceback.print_exc(file=sys.stderr)
            self.send({"id": rid, "ok": False, "error": {"code": "internal", "message": f"內部錯誤：{type(ex).__name__}: {ex}"}})
        finally:
            self._cancelled.discard(rid)

    def serve(self) -> int:
        threading.Thread(target=self._reader, name="stdin-reader", daemon=True).start()
        self.send({"event": "ready", "version": __version__, "tesseract": find_tesseract() is not None})
        while self._running:
            req = self._queue.get()
            if req is None:
                break
            self.handle(req)
        self.session.dispose()
        return 0


def main() -> int:
    # The shell reads UTF-8; Windows consoles default to a code page otherwise.
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="\n", line_buffering=True)
    sys.stdout = sys.stderr  # stray prints from libraries must not corrupt the protocol
    return Server(stdin, stdout).serve()


if __name__ == "__main__":
    raise SystemExit(main())
