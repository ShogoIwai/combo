#!/usr/bin/env python3
"""pdf_prepare.py — deterministic stage of the structured PDF conversion.

Usage:
    pdf_prepare.py <input.pdf> <workdir> [--pages N-M] [--password PW] [--dpi 150]

Writes into <workdir> (created if missing):
    raw.md        the default page-by-page conversion (convert.sh), unchanged
    blocks.jsonl  meta line, then one `page` record per page and one `block`
                  record per text block, each with a source address
                  (p<N> / p<N>.b<K>), bounding box in PDF points, text, and a
                  role: body | header | footer (running headers/footers are
                  detected as text repeated near the top/bottom of many pages)
    pages/page-<NNN>.png   page renders for pages routed `layout` or `vision`

Routing per page (the `route` field of a page record):
    text    text layer present, prose-like: structure it from the blocks
    layout  text layer present but table- or figure-heavy: copy text from the
            blocks, use the page image only to recover layout
    vision  (almost) no text layer: transcribe from the page image; the result
            cannot be checked against a text baseline and is reported as such
On text/layout pages, text read from a picture (not in the text layer) must be
wrapped in <!-- vision --> ... <!-- /vision --> so the gate can tell it apart.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from xml.etree import ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
XH = "{http://www.w3.org/1999/xhtml}"


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def extractor_rev():
    try:
        rev = sh(["git", "-C", HERE, "rev-parse", "--short", "HEAD"], check=True).stdout.strip()
        dirty = sh(["git", "-C", HERE, "status", "--porcelain", "--", "."], check=True).stdout.strip()
        return rev + ("-dirty" if dirty else "")
    except Exception:
        return "unknown"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def norm_hf(text):
    """Normalize a block for running header/footer matching (page numbers vary)."""
    return re.sub(r"\d+", "#", re.sub(r"\s+", "", text))


def image_stats(pdf, first, last, pw):
    """Per page: (count, largest image area in pixels) from pdfimages -list."""
    out = sh(["pdfimages"] + pw + ["-list", "-f", str(first), "-l", str(last), pdf]).stdout
    stats = {}
    for line in out.splitlines()[2:]:
        c = line.split()
        if len(c) < 5 or not c[0].isdigit():
            continue
        page, w, h = int(c[0]), int(c[3]), int(c[4])
        n, big = stats.get(page, (0, 0))
        stats[page] = (n + 1, max(big, w * h))
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("workdir")
    ap.add_argument("--pages")
    ap.add_argument("--password")
    ap.add_argument("--dpi", type=int, default=150)
    a = ap.parse_args()

    pw = ["-upw", a.password] if a.password else []
    os.makedirs(os.path.join(a.workdir, "pages"), exist_ok=True)

    # 1. the unchanged default conversion, kept as raw material
    cmd = ["bash", os.path.join(HERE, "convert.sh"), a.input, os.path.join(a.workdir, "raw.md")]
    if a.pages:
        cmd += ["--pages", a.pages]
    if a.password:
        cmd += ["--password", a.password]
    r = sh(cmd)
    if r.returncode:
        sys.stderr.write(r.stderr)
        sys.exit(r.returncode)

    info = sh(["pdfinfo"] + pw + [a.input]).stdout
    total = int(re.search(r"^Pages:\s+(\d+)", info, re.M).group(1))
    first, last = (map(int, a.pages.split("-")) if a.pages else (1, total))

    # 2. positioned text
    x = sh(["pdftotext"] + pw + ["-bbox-layout", "-f", str(first), "-l", str(last), a.input, "-"]).stdout
    root = ET.fromstring(re.sub(r"<!DOCTYPE[^>]*>", "", x))
    pages = []
    for pi, pg in enumerate(root.iter(XH + "page")):
        pno = first + pi
        W, H = float(pg.get("width")), float(pg.get("height"))
        blocks = []
        for b in pg.iter(XH + "block"):
            lines = [" ".join(w.text or "" for w in ln.iter(XH + "word")) for ln in b.iter(XH + "line")]
            text = "\n".join(l for l in lines if l.strip())
            if not text.strip():
                continue
            bbox = [round(float(b.get(k)), 1) for k in ("xMin", "yMin", "xMax", "yMax")]
            blocks.append({"bbox": bbox, "text": text})
        pages.append({"page": pno, "width": W, "height": H, "blocks": blocks})

    # 3. running headers / footers: same normalized text near top/bottom on many
    #    pages. Counted over the range widened by up to 10 pages each side, so a
    #    small range (even one page) still recognizes the document's furniture.
    s_first, s_last = max(1, first - 10), min(total, last + 10)
    if (s_first, s_last) != (first, last):
        xs = sh(["pdftotext"] + pw + ["-bbox-layout", "-f", str(s_first), "-l", str(s_last), a.input, "-"]).stdout
        stat_pages = []
        for pg in ET.fromstring(re.sub(r"<!DOCTYPE[^>]*>", "", xs)).iter(XH + "page"):
            H = float(pg.get("height"))
            stat_pages.append([(float(b.get("yMin")), float(b.get("yMax")), H,
                                " ".join(w.text or "" for w in b.iter(XH + "word"))) for b in pg.iter(XH + "block")])
    else:
        stat_pages = [[(b["bbox"][1], b["bbox"][3], p["height"], b["text"]) for b in p["blocks"]] for p in pages]
    band = Counter()
    for sp in stat_pages:
        for y0, y1, H, t in sp:
            if y1 < H * 0.1 or y0 > H * 0.9:
                band[norm_hf(t)] += 1
    need = max(2, int(len(stat_pages) * 0.3))
    for p in pages:
        for b in p["blocks"]:
            y0, y1 = b["bbox"][1], b["bbox"][3]
            top, bottom = y1 < p["height"] * 0.1, y0 > p["height"] * 0.9
            b["role"] = ("header" if top else "footer") if (top or bottom) and band[norm_hf(b["text"])] >= need else "body"

    # 4. routing
    imgs = image_stats(a.input, first, last, pw)
    for p in pages:
        body = [b for b in p["blocks"] if b["role"] == "body"]
        chars = sum(len(re.sub(r"\s", "", b["text"])) for b in body)
        short = sum(1 for b in body if len(b["text"]) < 25)
        n_img, big = imgs.get(p["page"], (0, 0))
        reasons = []
        if chars < 10:
            route = "vision"
            reasons.append(f"{chars} body chars in the text layer")
        else:
            route = "text"
            if chars < 80:
                route = "layout"
                reasons.append(f"sparse text layer ({chars} chars): look for text inside pictures")
            if len(body) >= 15 and short / max(1, len(body)) > 0.6:
                route = "layout"
                reasons.append(f"{short}/{len(body)} short blocks (table-like)")
            if big >= 200 * 200:
                route = "layout"
                reasons.append(f"{n_img} image(s), largest {big} px")
        p.update(chars=chars, n_body=len(body), images=n_img, route=route, reasons=reasons)

    # 5. renders for pages that need eyes
    for p in pages:
        if p["route"] == "text":
            continue
        stem = os.path.join(a.workdir, "pages", f"page-{p['page']:03d}")
        sh(["pdftoppm"] + pw + ["-r", str(a.dpi), "-png", "-singlefile", "-f", str(p["page"]),
            "-l", str(p["page"]), a.input, stem])
        p["image"] = os.path.relpath(stem + ".png", a.workdir) if os.path.exists(stem + ".png") else None

    ver = (sh(["pdftotext", "-v"]).stderr.splitlines() or ["?"])[0]
    meta = {"type": "meta", "source": os.path.abspath(a.input), "sha256": sha256(a.input),
            "extractor": "combo-fileconv-pdf-to-md/pdf_prepare.py", "extractor_rev": extractor_rev(),
            "libs": {"poppler": ver}, "pages": [first, last], "total_pages": total, "dpi": a.dpi,
            "routes": dict(Counter(p["route"] for p in pages))}
    with open(os.path.join(a.workdir, "blocks.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for p in pages:
            rec = {k: p[k] for k in ("page", "width", "height", "chars", "n_body", "images", "route", "reasons")}
            rec.update(type="record", kind="page", id=f"p{p['page']}", image=p.get("image"))
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            for bi, b in enumerate(p["blocks"]):
                f.write(json.dumps({"type": "record", "kind": "block", "id": f"p{p['page']}.b{bi}",
                                    "page": p["page"], "bbox": b["bbox"], "role": b["role"],
                                    "text": b["text"]}, ensure_ascii=False) + "\n")
    print(f"prepared {a.workdir}: pages {first}-{last}, routes {meta['routes']}")


if __name__ == "__main__":
    main()
