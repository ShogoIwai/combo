#!/usr/bin/env python3
"""xlsx_prepare.py — deterministic stage of the layout-aware xlsx conversion.

Usage:
    xlsx_prepare.py <input.xlsx> <workdir> [--sheets NAME,NAME] [--dpi 60]

Writes into <workdir>:
    raw.md         the default conversion (convert.sh), unchanged
    layout.jsonl   meta line, then per sheet: a `sheet` record, `region`
                   records (connected blocks of filled or bordered cells: the
                   separate tables / text blocks of a form or Excel-方眼紙 sheet),
                   `cell` records (display text as Excel shows it, raw value,
                   merge range, region), `shape` records (drawing text and
                   geometry) and `edge` records (connectors between shapes)
    draft.md       a deterministic structured draft: one section per sheet,
                   one block per region, a Mermaid flowchart built from the
                   connectors. It passes xlsx_check.py by construction and is
                   the starting point for the model
    sheets/sheet-<NN>.png   one render per visible sheet (LibreOffice)

Ids: sheet:<name>, region:<name>#<k>, cell:<name>!A1, shape:<name>#<n>
(drawing order), edge:<name>#<k>. Edge `how` is `attached` (the connector
names both shapes in the XML), `geometric` (an end resolved by position) or
`dangling` (an end touches no shape; not drawn in the draft).
"""
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import warnings
import zipfile
from collections import Counter
from xml.etree import ElementTree as ET

warnings.filterwarnings("ignore")
try:
    import openpyxl
    from openpyxl.utils import get_column_letter
except ImportError:
    sys.exit("Error: openpyxl is required")

HERE = os.path.dirname(os.path.abspath(__file__))
NS = {"xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
      "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
EMU_PX = 9525
TIMEOUT = 300


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def extractor_rev():
    try:
        rev = sh(["git", "-C", HERE, "rev-parse", "--short", "HEAD"], check=True).stdout.strip()
        dirty = sh(["git", "-C", HERE, "status", "--porcelain", "--", "."], check=True).stdout.strip()
        return rev + ("-dirty" if dirty else "")
    except Exception:
        return "unknown"


# ------------------------------------------------------------ LibreOffice
def libreoffice(src, work):
    """Per-sheet PDF (one page per visible sheet) and per-sheet CSV of the
    displayed text. Returns (pdf path or None, {sheet name: rows}, note)."""
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        return None, {}, "LibreOffice not found: no renders, display text = raw value"
    prof = tempfile.mkdtemp(prefix="lo-profile-")
    base = os.path.splitext(os.path.basename(src))[0]
    try:
        common = [exe, f"-env:UserInstallation=file://{prof}", "--headless", "--convert-to"]
        pdf_dir, csv_dir = os.path.join(work, "lo-pdf"), os.path.join(work, "lo-csv")
        sh(common + ['pdf:calc_pdf_Export:{"SinglePageSheets":{"type":"boolean","value":"true"}}',
                     "--outdir", pdf_dir, src], timeout=TIMEOUT)
        # 76 = UTF-8; 9th token true = "save cell contents as shown"; -1 = every sheet
        sh(common + ["csv:Text - txt - csv (StarCalc):44,34,76,1,,0,false,true,true,false,false,-1",
                     "--outdir", csv_dir, src], timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, {}, f"LibreOffice timed out after {TIMEOUT}s"
    finally:
        shutil.rmtree(prof, ignore_errors=True)
    pdf = os.path.join(pdf_dir, base + ".pdf")
    shown = {}
    for fn in os.listdir(csv_dir) if os.path.isdir(csv_dir) else []:
        if fn.startswith(base + "-") and fn.endswith(".csv"):
            with open(os.path.join(csv_dir, fn), encoding="utf-8", newline="") as f:
                shown[fn[len(base) + 1:-4]] = list(csv.reader(f))
    return (pdf if os.path.exists(pdf) else None), shown, None


# ------------------------------------------------------------ geometry
def col_px(ws, c):
    d = ws.column_dimensions.get(get_column_letter(c))
    if d is not None and d.hidden:
        return 0
    w = d.width if d is not None and d.width else (ws.sheet_format.defaultColWidth or 8.43)
    return int((256 * w + int(128 / 7)) / 256 * 7)


def row_px(ws, r):
    d = ws.row_dimensions.get(r)
    if d is not None and d.hidden:
        return 0
    h = d.height if d is not None and d.height else (ws.sheet_format.defaultRowHeight or 15)
    return int(round(h * 96 / 72))


class Grid:
    """Cell index -> EMU position (0-based col/row as in drawing anchors)."""

    def __init__(self, ws, ncol, nrow):
        self.x, self.y = [0], [0]
        for c in range(1, ncol + 2):
            self.x.append(self.x[-1] + col_px(ws, c) * EMU_PX)
        for r in range(1, nrow + 2):
            self.y.append(self.y[-1] + row_px(ws, r) * EMU_PX)
        self.ws = ws

    def cx(self, col0):
        while col0 >= len(self.x):
            self.x.append(self.x[-1] + col_px(self.ws, len(self.x)) * EMU_PX)
        return self.x[col0]

    def ry(self, row0):
        while row0 >= len(self.y):
            self.y.append(self.y[-1] + row_px(self.ws, len(self.y)) * EMU_PX)
        return self.y[row0]

    def point(self, m):
        return (self.cx(int(m.findtext("xdr:col", "0", NS))) + int(m.findtext("xdr:colOff", "0", NS)),
                self.ry(int(m.findtext("xdr:row", "0", NS))) + int(m.findtext("xdr:rowOff", "0", NS)))


def anchor_box(anchor, grid):
    tag = anchor.tag.split("}")[1]
    if tag == "twoCellAnchor":
        x0, y0 = grid.point(anchor.find("xdr:from", NS))
        x1, y1 = grid.point(anchor.find("xdr:to", NS))
    elif tag == "oneCellAnchor":
        x0, y0 = grid.point(anchor.find("xdr:from", NS))
        e = anchor.find("xdr:ext", NS)
        x1, y1 = x0 + int(e.get("cx")), y0 + int(e.get("cy"))
    else:  # absoluteAnchor
        p, e = anchor.find("xdr:pos", NS), anchor.find("xdr:ext", NS)
        x0, y0 = int(p.get("x")), int(p.get("y"))
        x1, y1 = x0 + int(e.get("cx")), y0 + int(e.get("cy"))
    return [x0, y0, x1, y1]


def xfrm_of(el):
    for path in ("xdr:spPr/a:xfrm", "xdr:grpSpPr/a:xfrm"):
        x = el.find(path, NS)
        if x is not None:
            return x
    return None


def sub_box(el, box, parent_xfrm):
    """Box of a group child: map its xfrm from the group's child space."""
    x, px = xfrm_of(el), parent_xfrm
    if x is None or px is None or px.find("a:chExt", NS) is None:
        return box
    o, e = x.find("a:off", NS), x.find("a:ext", NS)
    co, ce = px.find("a:chOff", NS), px.find("a:chExt", NS)
    sx = (box[2] - box[0]) / max(1, int(ce.get("cx")))
    sy = (box[3] - box[1]) / max(1, int(ce.get("cy")))
    x0 = box[0] + (int(o.get("x")) - int(co.get("x"))) * sx
    y0 = box[1] + (int(o.get("y")) - int(co.get("y"))) * sy
    return [round(x0), round(y0), round(x0 + int(e.get("cx")) * sx), round(y0 + int(e.get("cy")) * sy)]


def shape_text(el):
    paras = []
    for p in el.findall(".//xdr:txBody/a:p", NS):
        t = "".join(r.text or "" for r in p.iter("{%s}t" % NS["a"]))
        paras.append(t)
    return "\n".join(paras).strip()


def walk_drawing(root, grid):
    """Yield (kind, element, box) for sp/cxnSp/pic, descending into groups."""
    def rec(el, box, pxfrm):
        tag = el.tag.split("}")[1]
        if tag == "grpSp":
            gbox = sub_box(el, box, pxfrm) if pxfrm is not None else box
            gx = el.find("xdr:grpSpPr/a:xfrm", NS)
            for ch in el:
                if ch.tag.split("}")[1] in ("sp", "cxnSp", "pic", "grpSp"):
                    yield from rec(ch, gbox, gx)
        elif tag in ("sp", "cxnSp", "pic"):
            yield tag, el, (sub_box(el, box, pxfrm) if pxfrm is not None else box)
    for anchor in root:
        if anchor.tag.split("}")[1] not in ("twoCellAnchor", "oneCellAnchor", "absoluteAnchor"):
            continue
        box = anchor_box(anchor, grid)
        for ch in anchor:
            if ch.tag.split("}")[1] in ("sp", "cxnSp", "pic", "grpSp"):
                yield from rec(ch, box, None)


def drawing_for_sheets(z):
    """sheet name -> drawing part name, through workbook and sheet rels."""
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    wrels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = {r.get("Id"): r.get("Target") for r in wrels}
    out = {}
    for s in wb.iter("{%s}sheet" % NS["m"]):
        t = target.get(s.get("{%s}id" % NS["r"]), "")
        part = os.path.normpath(os.path.join("xl", t.lstrip("/").replace("xl/", "", 1))) if not t.startswith("/") else t.lstrip("/")
        rels = os.path.join(os.path.dirname(part), "_rels", os.path.basename(part) + ".rels")
        if rels not in z.namelist():
            continue
        for r in ET.fromstring(z.read(rels)):
            if r.get("Type", "").endswith("/drawing"):
                out[s.get("name")] = os.path.normpath(os.path.join(os.path.dirname(part), r.get("Target")))
    return out


def shapes_and_edges(z, part, grid, sheet):
    root = ET.fromstring(z.read(part))
    shapes, conns = [], []
    for kind, el, box in walk_drawing(root, grid):
        nv = el.find(".//xdr:cNvPr", NS)
        xid = nv.get("id") if nv is not None else None
        if kind == "cxnSp":
            ln = el.find("xdr:spPr/a:ln", NS)
            head = ln is not None and ln.find("a:headEnd", NS) is not None and ln.find("a:headEnd", NS).get("type", "none") != "none"
            tail = ln is not None and ln.find("a:tailEnd", NS) is not None and ln.find("a:tailEnd", NS).get("type", "none") != "none"
            x = el.find("xdr:spPr/a:xfrm", NS)
            fh = x is not None and x.get("flipH") in ("1", "true")
            fv = x is not None and x.get("flipV") in ("1", "true")
            p0 = (box[2] if fh else box[0], box[3] if fv else box[1])
            p1 = (box[0] if fh else box[2], box[1] if fv else box[3])
            st = el.find(".//a:stCxn", NS)
            en = el.find(".//a:endCxn", NS)
            conns.append({"p0": p0, "p1": p1, "st": st.get("id") if st is not None else None,
                          "en": en.get("id") if en is not None else None, "head": head, "tail": tail,
                          "text": shape_text(el)})
        else:
            geom = el.find("xdr:spPr/a:prstGeom", NS)
            shapes.append({"xml_id": xid, "kind": "image" if kind == "pic" else "shape",
                           "geom": geom.get("prst") if geom is not None else None,
                           "text": shape_text(el) if kind == "sp" else "", "box": box})
    for i, s in enumerate(shapes):
        s["id"] = f"shape:{sheet}#{i}"
    # copy-pasted shapes stacked on top of each other look like one: keep the
    # first, mark the rest so they are neither required nor connected
    def iou(p, q):
        w = min(p[2], q[2]) - max(p[0], q[0])
        h = min(p[3], q[3]) - max(p[1], q[1])
        if w <= 0 or h <= 0:
            return 0.0
        inter = w * h
        return inter / ((p[2] - p[0]) * (p[3] - p[1]) + (q[2] - q[0]) * (q[3] - q[1]) - inter)
    for i, s in enumerate(shapes):
        for t in shapes[:i]:
            if "duplicate_of" not in t and t["kind"] == s["kind"] and t["text"] == s["text"] and iou(s["box"], t["box"]) > 0.8:
                s["duplicate_of"] = t["id"]
                break
    shapes_by_id = {s['id']: s for s in shapes}
    by_xml = {s["xml_id"]: shapes_by_id[s["duplicate_of"]] if "duplicate_of" in s else s
              for s in shapes if s["xml_id"]}
    tol = 4 * EMU_PX * 3  # ~12 px

    def at(p):
        best = None
        for s in shapes:
            if s["kind"] != "shape" or "duplicate_of" in s:
                continue
            b = s["box"]
            dx = max(b[0] - p[0], 0, p[0] - b[2])
            dy = max(b[1] - p[1], 0, p[1] - b[3])
            d = max(dx, dy)
            if d <= tol and (best is None or d < best[0]):
                best = (d, s)
        return best[1] if best else None
    # Connectors form networks: a connector end that lies on another
    # connector (a T-junction or a shared end point) joins the two; a plain
    # crossing does not. In a network, shapes touched by an arrow-head end are
    # targets and shapes touched by a plain end are sources; every source
    # flows to every target (a bus that merges and forks).
    def on_seg(p, a, b):
        (x, y), (x0, y0), (x1, y1) = p, a, b
        dx, dy = x1 - x0, y1 - y0
        L = dx * dx + dy * dy
        t = 0 if L == 0 else max(0, min(1, ((x - x0) * dx + (y - y0) * dy) / L))
        return max(abs(x - (x0 + t * dx)), abs(y - (y0 + t * dy))) <= tol
    ends = []
    for c in conns:
        a = by_xml.get(c["st"]) if c["st"] else None
        b = by_xml.get(c["en"]) if c["en"] else None
        c["att"] = [a or at(c["p0"]), b or at(c["p1"])]
        c["xml"] = [a is not None, b is not None]
        c["arrow"] = [c["head"], c["tail"]]
    parent = list(range(len(conns)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, c in enumerate(conns):
        for k, p in enumerate((c["p0"], c["p1"])):
            if c["att"][k] is not None:
                continue
            for j, d in enumerate(conns):
                if j != i and on_seg(p, d["p0"], d["p1"]):
                    parent[find(i)] = find(j)
    nets = {}
    for i in range(len(conns)):
        nets.setdefault(find(i), []).append(conns[i])
    edges = []
    for net in nets.values():
        src, dst, plain = [], [], []
        has_arrow = any(any(c["arrow"]) for c in net)
        for ci, c in enumerate(net):
            for k in (0, 1):
                s = c["att"][k]
                if s is None:
                    continue
                (dst if c["arrow"][k] else (src if has_arrow else plain)).append((s, ci))
        label = " ".join(c["text"] for c in net if c["text"])
        if len(net) == 1:
            c = net[0]
            how = "attached" if all(c["xml"]) else "geometric"
        else:
            how = "network"
        # a self-loop is real only when it leaves and re-enters through
        # different connectors (e.g. a decision's "No" branch back into its input)
        pairs = [(a, b) for a, i in src for b, j in dst if a is not b or i != j]
        directed = has_arrow
        if not has_arrow:   # plain lines: every pair of shapes they join, undirected
            uniq = list({id(x): x for x, _ in plain}.values())
            pairs = [(uniq[i], uniq[j]) for i in range(len(uniq)) for j in range(i + 1, len(uniq))]
        seen = set()
        for a, b in pairs:
            if (a["id"], b["id"]) in seen:
                continue
            seen.add((a["id"], b["id"]))
            edges.append({"from": a["id"], "to": b["id"], "how": how, "directed": directed,
                          "label": label, "connectors": len(net)})
        if not pairs:
            touched = sorted({x["id"] for x, _ in src + dst + plain})
            edges.append({"from": None, "to": None, "how": "dangling", "directed": has_arrow,
                          "label": label, "connectors": len(net), "touches": touched})
    return shapes, edges


# ------------------------------------------------------------ regions
def regions_of(ws, filled, merges):
    """Connected components (4-neighbour) of filled or bordered cells; a merge
    range is one node."""
    occ = set(filled)
    for c in list(ws._cells.values()):   # stored cells only, never the used-range rectangle
        b = c.border
        if b is not None and any(getattr(b, s) is not None and getattr(b, s).style for s in ("left", "right", "top", "bottom")):
            occ.add((c.row, c.column))
    owner = {}
    for (r0, c0, r1, c1) in merges:
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                owner[(r, c)] = (r0, c0)
                if (r0, c0) in filled:
                    occ.add((r, c))
    seen, comps = set(), []
    for start in sorted(occ):
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            r, c = stack.pop()
            comp.append((r, c))
            for q in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                if q in occ and q not in seen:
                    seen.add(q)
                    stack.append(q)
        comps.append(comp)
    out = []
    for comp in comps:
        rs, cs = [p[0] for p in comp], [p[1] for p in comp]
        cells = sorted({owner.get(p, p) for p in comp if owner.get(p, p) in filled})
        if cells:
            out.append({"r0": min(rs), "c0": min(cs), "r1": max(rs), "c1": max(cs), "cells": cells})
    out.sort(key=lambda g: (g["r0"], g["c0"]))
    return out


def a1(r, c):
    return f"{get_column_letter(c)}{r}"


def md_cell(s):
    return s.strip().replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def mm_label(s):
    return s.replace('"', "#quot;").replace("\n", "<br>")


NODE = {"flowChartDecision": ('{"', '"}'), "diamond": ('{"', '"}'),
        "flowChartTerminator": ('(["', '"])'), "flowChartAlternateProcess": ('("', '")'),
        "roundRect": ('("', '")'), "ellipse": ('(("', '"))'), "flowChartConnector": ('(("', '"))')}


def mermaid(shapes, edges):
    used = [e for e in edges if e["how"] != "dangling"]
    if not used:
        return None
    ids = {}
    lines = ["flowchart TD"]
    for e in used:
        for sid in (e["from"], e["to"]):
            if sid not in ids:
                s = next(x for x in shapes if x["id"] == sid)
                ids[sid] = f"s{sid.rsplit('#', 1)[1]}"
                o, c = NODE.get(s["geom"], ('["', '"]'))
                lines.append(f"    {ids[sid]}{o}{mm_label(s['text'])}{c}")
    for e in used:
        arrow = "-->" if e["directed"] else "---"
        lab = f"|{mm_label(e['label'])}|" if e["label"] else ""
        lines.append(f"    {ids[e['from']]} {arrow}{lab} {ids[e['to']]}")
    return "\n".join(lines)


# ------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("workdir")
    ap.add_argument("--sheets", help="comma-separated sheet names (default: all visible)")
    ap.add_argument("--dpi", type=int, default=60)
    a = ap.parse_args()
    os.makedirs(os.path.join(a.workdir, "sheets"), exist_ok=True)

    r = sh(["bash", os.path.join(HERE, "convert.sh"), a.input, os.path.join(a.workdir, "raw.md")])
    if r.returncode:
        sys.stderr.write(r.stderr)
        sys.exit(r.returncode)

    pdf, shown, lo_note = libreoffice(a.input, a.workdir)
    wb = openpyxl.load_workbook(a.input)                       # formulas
    wbv = openpyxl.load_workbook(a.input, data_only=True)      # cached values
    z = zipfile.ZipFile(a.input)
    drawings = drawing_for_sheets(z)
    visible = [ws.title for ws in wb.worksheets if ws.sheet_state == "visible"]
    want = a.sheets.split(",") if a.sheets else visible
    unknown = [s for s in want if s not in wb.sheetnames]
    if unknown:
        sys.exit(f"Error: no such sheet: {', '.join(unknown)}")

    # renders: SinglePageSheets emits one page per sheet in workbook order,
    # hidden and empty sheets included (LibreOffice 24.2); fall back to visible
    # sheets only, and confirm each page by its text before attaching it
    images, notes = {}, [lo_note] if lo_note else []
    if pdf:
        n = int(re.search(r"^Pages:\s+(\d+)", sh(["pdfinfo", pdf]).stdout, re.M).group(1))
        order = wb.sheetnames if n == len(wb.sheetnames) else (visible if n == len(visible) else None)
        if order is None:
            notes.append(f"render has {n} pages for {len(wb.sheetnames)} sheets ({len(visible)} visible): no images attached")
        else:
            # Confirm the mapping by text: cells and drawing text of each sheet,
            # spaces removed (pdftotext splits words: "rd_v alid"). Sheets can
            # share text and pictures (EMF/OLE) hide theirs, so a single page
            # proves little; instead the mapping as a whole must fit better
            # than the same mapping shifted by one page either way.
            def sheet_text(nm):
                t = "".join(c for row in shown.get(nm, []) for c in row)
                if nm in drawings:
                    t += "".join(re.findall(r"<a:t>([^<]*)</a:t>", z.read(drawings[nm]).decode("utf-8", "replace")))
                return re.sub(r"\s+", "", t)
            texts = [sheet_text(nm) for nm in order]
            words = [re.findall(r"[A-Za-z0-9_]{4,}|[぀-ヿ㐀-鿿]{2,}",
                                sh(["pdftotext", "-f", str(k), "-l", str(k), pdf, "-"]).stdout) for k in range(1, n + 1)]

            def fit(shift):
                pairs = [(k, k + shift) for k in range(n) if 0 <= k + shift < n and words[k]]
                return sum(sum(1 for w in words[k] if w in texts[m]) / len(words[k]) for k, m in pairs) / max(1, len(pairs))
            if n > 1 and fit(0) < max(fit(-1), fit(1)):
                notes.append(f"render pages do not line up with the sheets (fit {fit(0):.2f} vs shifted "
                             f"{max(fit(-1), fit(1)):.2f}): no images attached")
                order = []
            for i, name in enumerate(order, 1):
                if name not in want:
                    continue
                stem = os.path.join(a.workdir, "sheets", f"sheet-{i:02d}")
                sh(["pdftoppm", "-r", str(a.dpi), "-png", "-singlefile", "-f", str(i), "-l", str(i), pdf, stem])
                if os.path.exists(stem + ".png"):
                    images[name] = os.path.relpath(stem + ".png", a.workdir)

    recs, draft = [], []
    for name in want:
        ws, wv = wb[name], wbv[name]
        rows = shown.get(name)
        merges = [(m.min_row, m.min_col, m.max_row, m.max_col) for m in ws.merged_cells.ranges]
        mrange = {(m[0], m[1]): m for m in merges}
        cells = {}
        # stored cells only (a stray format far away must not materialize the
        # whole rectangle); a formula cell exists in both workbooks
        for key in sorted(set(wv._cells) | set(ws._cells)):
            cv, cf = wv._cells.get(key), ws._cells.get(key)
            c = cv or cf
            v = cv.value if cv is not None else None
            f = cf.value if cf is not None else None
            disp = None
            if rows is not None and c.row - 1 < len(rows) and c.column - 1 < len(rows[c.row - 1]):
                disp = rows[c.row - 1][c.column - 1]
            if disp is None:
                disp = "" if v is None else str(v)
            formula = f if isinstance(f, str) and f.startswith("=") else None
            if disp.strip() == "" and v in (None, "") and formula is None:
                continue
            cells[(c.row, c.column)] = {"text": disp, "value": v, "formula": formula}
        filled = {k for k, x in cells.items() if x["text"].strip()}
        regs = regions_of(ws, filled, merges)
        grid = Grid(ws, 0, 0)   # grows lazily up to the anchors actually used
        shapes, edges = (shapes_and_edges(z, drawings[name], grid, name) if name in drawings else ([], []))

        n_merge_cells = sum((m[2] - m[0] + 1) * (m[3] - m[1] + 1) for m in merges)
        reasons = []
        if len(regs) > 1:
            reasons.append(f"{len(regs)} separate cell regions")
        if any(s["kind"] == "shape" and s["text"] for s in shapes):
            reasons.append(f"{sum(1 for s in shapes if s['text'])} text shape(s), {len(edges)} connector(s)")
        if any(s["kind"] == "image" for s in shapes):
            reasons.append(f"{sum(1 for s in shapes if s['kind'] == 'image')} picture(s)")
        if n_merge_cells > max(20, len(filled)):
            reasons.append(f"{len(merges)} merged ranges")
        route = "layout" if reasons else "table"
        recs.append({"type": "record", "kind": "sheet", "id": f"sheet:{name}", "name": name,
                     "state": ws.sheet_state, "route": route, "reasons": reasons, "image": images.get(name),
                     "cells": len(filled), "regions": len(regs), "shapes": len(shapes), "edges": len(edges)})
        rid = {}
        for k, g in enumerate(regs):
            ref = f"{a1(g['r0'], g['c0'])}:{a1(g['r1'], g['c1'])}"
            recs.append({"type": "record", "kind": "region", "id": f"region:{name}#{k}", "sheet": name,
                         "range": ref, "cells": len(g["cells"])})
            for p in g["cells"]:
                rid[p] = k
        for (r_, c_), x in sorted(cells.items()):
            if not x["text"].strip():
                continue
            m = mrange.get((r_, c_))
            rec = {"type": "record", "kind": "cell", "id": f"cell:{name}!{a1(r_, c_)}", "sheet": name,
                   "region": rid.get((r_, c_)), "text": x["text"]}
            if x["value"] is not None and str(x["value"]) != x["text"]:
                rec["value"] = x["value"] if isinstance(x["value"], (int, float, bool)) else str(x["value"])
            if x["formula"]:
                rec["formula"] = x["formula"]
            if m:
                rec["merge"] = f"{a1(m[0], m[1])}:{a1(m[2], m[3])}"
            recs.append(rec)
        for s in shapes:
            recs.append({"type": "record", "kind": s["kind"], "id": s["id"], "sheet": name, "geom": s["geom"],
                         "text": s["text"], "box_emu": s["box"],
                         **({"duplicate_of": s["duplicate_of"]} if "duplicate_of" in s else {})})
        for k, e in enumerate(edges):
            recs.append({"type": "record", "kind": "edge", "id": f"edge:{name}#{k}", "sheet": name, **e})

        # ---- deterministic draft
        draft.append(f"<!-- sheet: {name} -->\n## {name}\n")
        if images.get(name):
            draft.append(f"![{name}]({images[name]})\n")
        for k, g in enumerate(regs):
            cols = sorted({c for (_, c) in g["cells"]})
            rws = sorted({r_ for (r_, _) in g["cells"]})
            if len(cols) == 1 or len(g["cells"]) == 1:
                draft.append("\n".join(cells[p]["text"].strip() for p in g["cells"]) + "\n")
                continue
            grid_rows = [[md_cell(cells[(r_, c_)]["text"]) if (r_, c_) in g["cells"] else "" for c_ in cols] for r_ in rws]
            out = ["| " + " | ".join(grid_rows[0]) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
            out += ["| " + " | ".join(rr) + " |" for rr in grid_rows[1:]]
            draft.append("\n".join(out) + "\n")
        mm = mermaid(shapes, edges)
        if mm:
            draft.append("```mermaid\n" + mm + "\n```\n")
        drawn = {e[k] for e in edges if e["how"] != "dangling" for k in ("from", "to")}
        rest = [s["text"] for s in shapes if s["kind"] == "shape" and s["text"] and s["id"] not in drawn
                and "duplicate_of" not in s]
        if rest:
            draft.append("\n".join("> " + t.replace("\n", "<br>") for t in rest) + "\n")

    meta = {"type": "meta", "source": os.path.abspath(a.input), "sha256": sha256(a.input),
            "extractor": "combo-fileconv-xlsx-to-md/xlsx_prepare.py", "extractor_rev": extractor_rev(),
            "libs": {"openpyxl": openpyxl.__version__,
                     "libreoffice": (sh(["soffice", "--version"]).stdout.strip() if shutil.which("soffice") else None)},
            "sheets": want, "visible": visible, "notes": notes,
            "routes": dict(Counter(r["route"] for r in recs if r["kind"] == "sheet"))}
    with open(os.path.join(a.workdir, "layout.jsonl"), "w", encoding="utf-8") as f:
        for rec in [meta] + recs:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    with open(os.path.join(a.workdir, "draft.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(draft))
    shutil.rmtree(os.path.join(a.workdir, "lo-csv"), ignore_errors=True)
    for x in notes:
        print("Note:", x, file=sys.stderr)
    print(f"prepared {a.workdir}: {len(want)} sheet(s), routes {meta['routes']}")


if __name__ == "__main__":
    main()
