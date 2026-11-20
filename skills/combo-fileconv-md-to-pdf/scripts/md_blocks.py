#!/usr/bin/env python3
"""md_blocks.py — the small Markdown parsing shared by convert.sh's staging,
md_prerender.py and md_pdf_qa.py, so rendering and checking see the same
blocks and images.

- fences(text): fenced code blocks per CommonMark (``` or ~~~, length >= 3,
  indent <= 3, closed by the same character at least as long), so a Mermaid
  example inside a longer fence stays an example.
- images(text): local and remote image destinations outside code: inline
  `![a](dest "t")` and `![a](<dest with spaces>)`, reference style
  `![a][ref]` / `![ref]` with `[ref]: dest`, and HTML `<img src="...">`.

Usage as a script (staging step of convert.sh):
    md_blocks.py stage <src.md> <out.md> [base_dir]
writes a copy whose local images are inlined as data: URIs, so the renderer
can serve an empty private directory instead of the source tree.
"""
import base64
import mimetypes
import os
import re
import sys
from urllib.parse import unquote

FENCE_OPEN = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")


def fences(text):
    """[(start_offset, end_offset, info, body)] of closed or unclosed fences
    (an unclosed fence runs to the end of the document, as in CommonMark)."""
    out, pos, cur = [], 0, None
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\n")
        if cur is None:
            m = FENCE_OPEN.match(bare)
            if m and not (m.group(2)[0] == "`" and "`" in m.group(3)):
                cur = {"start": pos, "ch": m.group(2)[0], "n": len(m.group(2)), "ind": len(m.group(1)),
                       "info": m.group(3).strip(), "body_start": pos + len(line)}
        else:
            m = re.match(r"^ {0,3}(`{3,}|~{3,})\s*$", bare)
            if m and m.group(1)[0] == cur["ch"] and len(m.group(1)) >= cur["n"]:
                out.append((cur["start"], pos + len(line), cur["info"], _body(text[cur["body_start"]:pos], cur["ind"])))
                cur = None
        pos += len(line)
    if cur is not None:
        out.append((cur["start"], len(text), cur["info"], _body(text[cur["body_start"]:], cur["ind"])))
    return out


def _body(body, ind):
    """Fence content with the opener's indentation (up to ind spaces) removed."""
    return "".join(re.sub(r"^ {0,%d}" % ind, "", l) if ind else l
                   for l in body.splitlines(keepends=True))


def mermaid_blocks(text):
    return [f for f in fences(text) if (f[2].split() or [""])[0].lower() == "mermaid"]


def _mask(text):
    """Text with code fences and inline code blanked (same length)."""
    chars = list(text)
    for s, e, _, _ in fences(text):
        for i in range(s, e):
            if chars[i] != "\n":
                chars[i] = " "
    masked = "".join(chars)
    return re.sub(r"(`+)(?:(?!\1).)+?\1", lambda m: " " * len(m.group(0)), masked)


INLINE = re.compile(r"!\[(?:[^\]\\]|\\.)*\]\(\s*(<[^>\n]*>|[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)")
REFIMG = re.compile(r"!\[((?:[^\]\\]|\\.)*)\](?:\[((?:[^\]\\]|\\.)*)\])?")
REFDEF = re.compile(r"(?m)^ {0,3}\[((?:[^\]\\]|\\.)+)\]:\s*(<[^>\n]*>|\S+)")
HTMLIMG = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*([\"'])(.*?)\1", re.I)


def _dest(d):
    d = d.strip()
    if d.startswith("<") and d.endswith(">"):
        d = d[1:-1]
    return unquote(re.sub(r"\\(.)", r"\1", d))


def images(text):
    """[(start, end, dest, kind)] — kind: inline | ref | html; for ref, the
    span is the reference definition's destination."""
    m_text = _mask(text)
    found = []
    for m in INLINE.finditer(m_text):
        found.append((m.start(1), m.end(1), _dest(text[m.start(1):m.end(1)]), "inline"))
    defs = {}
    for m in REFDEF.finditer(m_text):
        defs.setdefault(m.group(1).strip().lower(), (m.start(2), m.end(2)))
    used = set()
    for m in REFIMG.finditer(m_text):
        if m_text[m.end():m.end() + 1] == "(":
            continue                                   # inline form, handled above
        label = (m.group(2) if m.group(2) else m.group(1)).strip().lower()
        if label in defs and label not in used:
            used.add(label)
            s, e = defs[label]
            found.append((s, e, _dest(text[s:e]), "ref"))
    for m in HTMLIMG.finditer(m_text):
        found.append((m.start(2), m.end(2), _dest(text[m.start(2):m.end(2)]), "html"))
    return sorted(found)


def is_local(dest):
    return not re.match(r"[a-z][a-z0-9+.-]*:|//|#", dest, re.I)


def stage(src, out, base=None):
    """Copy src to out with local images inlined; links resolve against base
    (default: src's directory)."""
    text = open(src, encoding="utf-8").read()
    base = base or os.path.dirname(os.path.abspath(src))
    pieces, last, missing = [], 0, []
    for s, e, dest, _ in images(text):
        if not is_local(dest):
            continue
        path = os.path.normpath(os.path.join(base, dest.split("#")[0].split("?")[0]))
        if not os.path.isfile(path):
            missing.append(dest)
            continue
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        uri = f"data:{mime};base64," + base64.b64encode(open(path, "rb").read()).decode()
        pieces += [text[last:s], f"<{uri}>" if text[s:e].startswith("<") else uri]
        last = e
    pieces.append(text[last:])
    with open(out, "w", encoding="utf-8") as f:
        f.write("".join(pieces))
    for d in missing:
        print(f"Warning: image not found: {d}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) in (4, 5) and sys.argv[1] == "stage":
        stage(*sys.argv[2:])
    else:
        sys.exit("usage: md_blocks.py stage <src.md> <out.md> [base_dir]")
