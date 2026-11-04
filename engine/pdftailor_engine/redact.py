"""Removing text from a page, including text inside form XObjects.

Form XObjects can be shared between pages. Rewriting one in place would silently delete
the same text from every other page that uses it, so a changed form is *copied* and the
copy is attached to a private resources dictionary on the page being edited.
"""

from __future__ import annotations

import zlib

import pikepdf

from .contentstream import IDENTITY, Matrix, Region, mmul, redact_stream

_MAX_DEPTH = 4
_STREAM_ONLY_KEYS = {"/Length", "/Filter", "/DecodeParms", "/DL"}


def _inherited(obj, key: str):
    seen = 0
    while obj is not None and seen < 64:
        if key in obj:
            return obj[key]
        obj = obj.get("/Parent")
        seen += 1
    return None


def page_content_bytes(page: pikepdf.Page) -> bytes:
    """The page's content, joined if it is an array — without rewriting the page."""
    contents = page.obj.get("/Contents")
    if contents is None:
        return b""
    if isinstance(contents, pikepdf.Array):
        # Parts are concatenated with a separator: a token may not span two parts.
        return b"\n".join(part.read_bytes() for part in contents)
    return contents.read_bytes()


def flate_stream(pdf: pikepdf.Pdf, data: bytes) -> pikepdf.Stream:
    """A new Flate-compressed stream. The document is saved with compress_streams=False so
    untouched streams keep their exact bytes; streams we create are compressed here."""
    s = pdf.make_stream(b"")
    s.write(zlib.compress(data, 6), filter=pikepdf.Name.FlateDecode)
    return s


def _shallow_dict(d) -> pikepdf.Dictionary:
    return pikepdf.Dictionary({k: v for k, v in d.items()})


def _private_resources(res, replaced: dict[str, pikepdf.Object]) -> pikepdf.Dictionary:
    """A copy of ``res`` whose /XObject entries are overridden by ``replaced``."""
    new_res = _shallow_dict(res) if res is not None else pikepdf.Dictionary()
    xobjs = _shallow_dict(new_res["/XObject"]) if "/XObject" in new_res else pikepdf.Dictionary()
    for name, obj in replaced.items():
        xobjs[name] = obj
    new_res["/XObject"] = xobjs
    return new_res


def _walk(
    pdf: pikepdf.Pdf,
    owner,
    data: bytes,
    ctm: Matrix,
    regions: list[Region],
    depth: int,
    parent_res,
) -> tuple[bytes | None, int, dict[str, pikepdf.Object]]:
    """Redacts one stream and the forms it draws.

    Returns ``(new_bytes_or_None, removed_runs, replaced_forms)``; ``replaced_forms``
    maps XObject names to freshly built copies that the caller must attach.
    """
    res = owner.get("/Resources", parent_res) if hasattr(owner, "get") else parent_res
    result = redact_stream(data, regions, ctm)
    removed = result.removed_runs
    new_bytes = result.content if result.changed else None
    replaced: dict[str, pikepdf.Object] = {}

    if depth < _MAX_DEPTH and res is not None and "/XObject" in res:
        xobjs = res["/XObject"]
        for name, call_ctm in result.xobject_calls:
            key = "/" + name
            if key in replaced or key not in xobjs:
                continue
            xo = xobjs[key]
            if str(xo.get("/Subtype", "")) != "/Form":
                continue
            matrix: Matrix = tuple(float(v) for v in xo.get("/Matrix", IDENTITY))  # type: ignore[assignment]
            child_data = xo.read_bytes()
            nb, n, child_replaced = _walk(pdf, xo, child_data, mmul(matrix, call_ctm), regions, depth + 1, res)
            removed += n
            if nb is None and not child_replaced:
                continue
            copy = flate_stream(pdf, nb if nb is not None else child_data)
            for k, v in xo.items():
                if k not in _STREAM_ONLY_KEYS:
                    copy[k] = v
            if child_replaced:
                copy["/Resources"] = _private_resources(xo.get("/Resources", res), child_replaced)
            replaced[key] = copy
    return new_bytes, removed, replaced


def redact_page(pdf: pikepdf.Pdf, page: pikepdf.Page, regions: list[Region]) -> int:
    """Empties every show-text string whose origin falls in ``regions`` (user space).

    Returns the number of text runs removed.
    """
    if not regions:
        return 0
    data = page_content_bytes(page)
    res = _inherited(page.obj, "/Resources")
    new_bytes, removed, replaced = _walk(pdf, page.obj, data, IDENTITY, regions, 0, res)
    if new_bytes is not None:
        page.obj["/Contents"] = flate_stream(pdf, new_bytes)
    if replaced:
        page.obj["/Resources"] = _private_resources(res, replaced)
    return removed
