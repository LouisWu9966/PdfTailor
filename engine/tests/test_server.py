"""The JSON Lines protocol, driven through a real subprocess like the WPF shell does."""
import json
import os
import subprocess
import sys
import threading

import pytest

from fixtures import TextItem, make_text_pdf

ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Client:
    def __init__(self, tmp):
        env = dict(os.environ, LOCALAPPDATA=str(tmp), PYTHONPATH=ENGINE_DIR)
        self.p = subprocess.Popen(
            [sys.executable, "-m", "pdftailor_engine"], cwd=ENGINE_DIR, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        )
        self.ready = json.loads(self.p.stdout.readline())
        self.events = []

    def send(self, i, cmd, **args):
        self.p.stdin.write(json.dumps({"id": i, "cmd": cmd, "args": args}, ensure_ascii=False) + "\n")
        self.p.stdin.flush()

    def recv(self, i):
        while True:
            m = json.loads(self.p.stdout.readline())
            if "event" in m:
                self.events.append(m)
                continue
            if m.get("id") == i:
                return m

    def call(self, i, cmd, **args):
        self.send(i, cmd, **args)
        return self.recv(i)

    def close(self):
        try:
            self.call(999, "shutdown")
            self.p.wait(10)
        finally:
            if self.p.poll() is None:
                self.p.kill()


@pytest.fixture
def client(tmp_path):
    c = Client(tmp_path)
    yield c
    c.close()


def test_ready_and_errors(client):
    assert client.ready["event"] == "ready" and "version" in client.ready
    assert client.call(1, "ping")["result"] == {"pong": True}
    r = client.call(2, "nope")
    assert r["ok"] is False and r["error"]["code"] == "bad-request"
    r = client.call(3, "open", path="/definitely/missing.pdf")
    assert r["ok"] is False and r["error"]["code"] == "user"
    r = client.call(4, "analyze", page=1)
    assert r["error"]["code"] == "user"  # nothing open yet
    client.p.stdin.write("this is not json\n")
    client.p.stdin.flush()
    assert client.call(5, "ping")["ok"]  # still serving


def test_open_analyze_preview_export(client, tmp_path):
    src = make_text_pdf(str(tmp_path / "a.pdf"), [TextItem(72, 100, "Invoice total 1200", size=14)])
    o = client.call(1, "open", path=src)
    assert o["ok"] and o["result"]["pageCount"] == 1 and o["result"]["pages"][0]["textLayer"] == "visible"
    a = client.call(2, "analyze", page=1)
    assert a["ok"] and a["result"]["mode"] == "text"
    assert any(e["event"] == "progress" and e["id"] == 2 for e in client.events)
    b = a["result"]["blocks"][0]
    edit = dict(b, originalText=b["text"], originalBounds=b["bounds"], text="Invoice total 3400")
    pv = client.call(3, "preview", edits=[edit])
    assert pv["ok"] and os.path.isfile(pv["result"]["images"][0]["path"])
    out = str(tmp_path / "out.pdf")
    ex = client.call(4, "export", edits=[edit], out=out)
    assert ex["ok"] and ex["result"]["verify"]["ok"] and os.path.isfile(out)
    r = client.call(5, "export", edits=[edit], out=src)
    assert r["error"]["code"] == "user"  # never overwrite the original
    f = client.call(6, "fonts")
    assert f["ok"] and len(f["result"]["families"]) > 3
