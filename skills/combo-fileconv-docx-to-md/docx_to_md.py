#!/usr/bin/env python3
"""docx_to_md.py — body of combo-fileconv-docx-to-md/convert.sh.

Walks the document body in source order (paragraphs and tables interleaved,
including content inside body-level content controls) and writes Markdown.

Text is taken in the *accepted* view of tracked changes: inserted runs are
kept, deleted runs (w:delText) and field instructions (w:instrText) are not.
Text boxes are emitted right after the paragraph that anchors them.

Usage:
    docx_to_md.py <input.docx> <output.md> [--records <records.jsonl>]

--records writes one JSON object per line: first a "meta" record (source
path, sha256, extractor revision, library versions), then one record per
emitted source element with a stable source address (b<N> for body blocks,
t<K>.r<I>.c<J> for table cells, b<N>.x<M> for text boxes). Downstream
consistency checks use these addresses; the Markdown itself is unchanged by
the flag.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

try:
    import docx
    from docx import Document
    from docx.oxml.ns import qn
except ImportError:
    print("Error: python-docx is required (pip install python-docx)", file=sys.stderr)
    sys.exit(1)

W_P, W_TBL, W_SDT, W_SDTCONTENT = qn("w:p"), qn("w:tbl"), qn("w:sdt"), qn("w:sdtContent")
W_T, W_TAB, W_BR, W_CR = qn("w:t"), qn("w:tab"), qn("w:br"), qn("w:cr")
W_TXBX = qn("w:txbxContent")
W_TR, W_TC = qn("w:tr"), qn("w:tc")
W_PPR, W_OUTLINE, W_VAL = qn("w:pPr"), qn("w:outlineLvl"), qn("w:val")
MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
# Subtrees that are not part of the accepted view: deleted and moved-away runs.
W_DEL, W_MOVEFROM = qn("w:del"), qn("w:moveFrom")
NOT_ACCEPTED = (W_DEL, W_MOVEFROM)
BODY_TEXT = -1  # explicit outlineLvl 9: "body text", stops style inheritance

HEADING_RE = re.compile(r"heading\s+(\d+)$", re.I)
TOC_RE = re.compile(r"toc\s+(\d+)$", re.I)


# ---------------------------------------------------------------- body walk
def iter_blocks(container):
    """Yield w:p / w:tbl elements in document order, descending into w:sdt."""
    for child in container.iterchildren():
        if child.tag in (W_P, W_TBL):
            yield child
        elif child.tag == W_SDT:
            content = child.find(W_SDTCONTENT)
            if content is not None:
                yield from iter_blocks(content)


def _inside(el, tags, stop):
    """True if `el` has an ancestor whose tag is in `tags`, below `stop`."""
    a = el.getparent()
    while a is not None and a is not stop:
        if a.tag in tags:
            return True
        a = a.getparent()
    return False


def para_text(p):
    """Accepted-view text of one w:p, excluding text boxes it anchors."""
    out = []
    for el in p.iter(W_T, W_TAB, W_BR, W_CR):
        if _inside(el, (W_TXBX, MC_FALLBACK) + NOT_ACCEPTED, p):
            continue
        if el.tag == W_T:
            out.append(el.text or "")
        elif el.tag == W_TAB:
            out.append("\t")
        else:
            out.append("\n")
    return "".join(out)


def textboxes(p):
    """Paragraph texts of each text box anchored in p (Fallback copies skipped)."""
    boxes = []
    for tx in p.iter(W_TXBX):
        if _inside(tx, (MC_FALLBACK, W_TXBX) + NOT_ACCEPTED, p):
            continue
        lines = [para_text(q).strip() for q in tx.iter(W_P)]
        lines = [l for l in lines if l]
        if lines:
            boxes.append(lines)
    return boxes


# ---------------------------------------------------------------- headings
def _outline_of(ppr):
    if ppr is None:
        return None
    ol = ppr.find(W_OUTLINE)
    if ol is None:
        return None
    try:
        v = int(ol.get(W_VAL))
    except (TypeError, ValueError):
        return None
    if v == 9:
        return BODY_TEXT
    return v if 0 <= v <= 8 else None


def style_of(p, styles_by_id):
    ppr = p.find(W_PPR)
    sid = None
    if ppr is not None:
        ps = ppr.find(qn("w:pStyle"))
        if ps is not None:
            sid = ps.get(W_VAL)
    return styles_by_id.get(sid) if sid else styles_by_id.get(None)


def classify(p, style):
    """Return ('toc', n) | ('heading', n) | ('body', None)."""
    name = style.name if style is not None else ""
    m = TOC_RE.match(name or "")
    if m:
        return "toc", int(m.group(1))
    # The outline level decides, as in Word's navigation pane: the paragraph's
    # own w:outlineLvl first, then the nearest one in its style chain (so a
    # "Heading 2" paragraph set to Body Text, or to level 3, follows that).
    lvl = _outline_of(p.find(W_PPR))
    s = style
    seen = set()
    while lvl is None and s is not None and id(s) not in seen:
        seen.add(id(s))
        lvl = _outline_of(s.element.find(W_PPR))
        s = s.base_style
    if lvl == BODY_TEXT:
        return "body", None
    if lvl is not None:
        return "heading", lvl + 1
    # no outline level anywhere: a built-in "Heading N" style name
    m = HEADING_RE.match(name or "")
    if m:
        return "heading", int(m.group(1))
    return "body", None


# ---------------------------------------------------------------- tables
def md_cell(s):
    s = s.strip().replace("\\", "\\\\").replace("|", "\\|")
    return s.replace("\r", "").replace("\n", "<br>").replace("\t", " ")


def table_rows(tbl):
    """Rows of cell texts; a horizontally merged cell is repeated per grid
    column (python-docx semantics), nested tables are flattened into the cell."""
    t = docx.table.Table(tbl, None)
    rows = []
    for row in t.rows:
        trpr = row._tr.find(qn("w:trPr"))
        if trpr is not None and trpr.find(W_DEL) is not None:
            continue   # a tracked row deletion: not part of the accepted view
        cells = []
        for c in row.cells:
            texts = [para_text(q) for q in c._tc.iter(W_P) if not _inside(q, (W_TXBX,), c._tc)]
            # text boxes anchored inside the cell (e.g. cover-sheet TO/FROM boxes)
            texts += [l for box in textboxes(c._tc) for l in box]
            cells.append("\n".join(x.strip() for x in texts if x.strip()))
        rows.append(cells)
    return rows


def table_md(rows):
    if not rows:
        return ""
    n = max(len(r) for r in rows)
    lines = []
    for i, r in enumerate(rows):
        cells = [md_cell(c) for c in r] + [""] * (n - len(r))
        lines.append("| " + " | ".join(cells) + " |")
        if i == 0:
            lines.append("| " + " | ".join(["---"] * n) + " |")
    return "\n".join(lines) + "\n\n"


# ---------------------------------------------------------------- provenance
def extractor_rev():
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        rev = subprocess.run(["git", "-C", here, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", here, "status", "--porcelain", "--", "."],
                               capture_output=True, text=True, check=True).stdout.strip()
        return rev + ("-dirty" if dirty else "")
    except Exception:
        return "unknown"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--records")
    a = ap.parse_args()

    d = Document(a.input)
    styles_by_id = {s.style_id: s for s in d.styles if s.type == 1}  # paragraph styles
    default = next((s for s in d.styles if s.type == 1 and s.element.get(qn("w:default")) in ("1", "true")), None)
    styles_by_id[None] = default

    md, recs = [], []
    n_table = 0
    for bi, el in enumerate(iter_blocks(d.element.body)):
        bid = f"b{bi}"
        if el.tag == W_TBL:
            n_table += 1
            rows = table_rows(el)
            md.append(table_md(rows))
            for ri, r in enumerate(rows):
                for ci, c in enumerate(r):
                    recs.append({"id": f"t{n_table}.r{ri}.c{ci}", "kind": "cell", "block": bid,
                                 "table": n_table, "row": ri, "col": ci, "text": c})
            continue

        style = style_of(el, styles_by_id)
        kind, level = classify(el, style)
        text = para_text(el).strip()
        if text:
            flat = re.sub(r"\s*\n\s*", " ", text)
            if kind == "heading":
                md.append("#" * min(level, 6) + " " + flat + "\n\n")
            elif kind == "toc":
                # TOC entries are navigation, not structure: keep the text, never a heading.
                md.append("  " * (level - 1) + "- " + flat.replace("\t", " ") + "\n\n")
            else:
                md.append(text + "\n\n")
            recs.append({"id": bid, "kind": kind, "level": level,
                         "style": style.name if style is not None else None, "text": text})
        for xi, box in enumerate(textboxes(el)):
            md.append("".join("> " + l + "\n" for l in box) + "\n")
            recs.append({"id": f"{bid}.x{xi}", "kind": "textbox", "block": bid, "text": "\n".join(box)})

    out = "".join(md).rstrip() + "\n"
    with open(a.output, "w", encoding="utf-8") as f:
        f.write(out)

    if a.records:
        meta = {"type": "meta", "source": os.path.abspath(a.input), "sha256": sha256(a.input),
                "extractor": "combo-fileconv-docx-to-md/docx_to_md.py", "extractor_rev": extractor_rev(),
                "libs": {"python-docx": getattr(docx, "__version__", "?")},
                "view": "accepted-changes"}
        with open(a.records, "w", encoding="utf-8") as f:
            f.write(json.dumps(meta, ensure_ascii=False) + "\n")
            for r in recs:
                f.write(json.dumps(dict(type="record", **r), ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
