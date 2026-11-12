#!/usr/bin/env python3
"""xlsx_check.py — consistency gate for a layout-aware xlsx conversion.

Usage:
    xlsx_check.py <structured.md> <layout.jsonl> [--min-coverage 0.97] [--max-extra 0.03] [--report out.json]

The Markdown starts each prepared sheet with an anchor line
`<!-- sheet: NAME -->`, in workbook order. As in the PDF gate, text between
`<!-- interpretation -->` and `<!-- /interpretation -->` is excluded from
every check, and text read from the render (a picture, an unattached arrow)
goes between `<!-- vision -->` and `<!-- /vision -->` and is reported as
unverified. Inside a Mermaid block the same is written as `%% vision` /
`%% /vision` lines.

Checks per sheet (source = displayed cell text, shape text and connector
labels of that sheet; raw values count as source too):
  G1 anchors    one anchor per prepared sheet, in order
  G2 omission   every key token (digit, '_' or ALL-CAPS) occurs at least as
                often as in the source
  G3 coverage   share of source tokens found >= --min-coverage
  G4 invention  no key token absent from the source; other extra tokens at
                most --max-extra of the section's tokens
  G5 edges      every Mermaid edge (outside vision) joins two shapes the
                source connects, by their text; and once a sheet has a Mermaid
                flowchart, every connector edge of the source is in it
  G6 assets     every image link resolves
Consistency checks, not proof of fidelity: a value moved to another row with
the same spelling passes. Exit 1 on any FAIL.
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter

ANCHOR = re.compile(r"^<!--\s*sheet:\s*(.*?)\s*-->\s*$", re.M)
INTERP = re.compile(r"<!--\s*interpretation\s*-->.*?<!--\s*/interpretation\s*-->", re.S)
VISION = re.compile(r"<!--\s*vision\s*-->.*?<!--\s*/vision\s*-->", re.S)
MM_VISION = re.compile(r"^\s*%%\s*vision\s*$.*?^\s*%%\s*/vision\s*$", re.S | re.M)
MERMAID = re.compile(r"^```mermaid\s*\n(.*?)^```\s*$", re.S | re.M)
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)[^)]*\)")
TOKEN = re.compile(r"[A-Za-z0-9_]+|[぀-ヿ㐀-鿿豈-﫿]")
NODE_Q = re.compile(r"\b([A-Za-z_]\w*)\s*[\[\(\{>]+\s*\"((?:[^\"])*)\"\s*[\]\)\}]+")
NODE_U = re.compile(r"\b([A-Za-z_]\w*)\s*[\[\(\{>]+([^\]\)\}\"]*)[\]\)\}]+")
LIST_NO = re.compile(r"(?m)^\s*\d+\.(?=\s)")
ARROW = re.compile(r"\s*(-->|---|-\.->|-\.-|==>|===|--o|--x)\s*(?:\|([^|]*)\|\s*)?")


def tokens(text):
    return TOKEN.findall(unicodedata.normalize("NFKC", text))


def seqs(toks, kmax=6):
    for k in range(2, kmax + 1):
        for i in range(len(toks) - k + 1):
            yield toks[i:i + k]


class Matcher:
    """Token matching that tolerates re-spacing without crossing token
    boundaries: "array 2" == "array2" and "CoreSight TM" == "CoreSight™"
    (adjacent tokens joined), but "ID1" is never found inside "ID10"."""

    def __init__(self, src, md):
        self.mc = Counter(md)
        self.mcat = Counter("".join(q) for q in seqs(md))        # md re-joined
        self.credit = Counter()                                   # md token = joined source run
        for q in seqs(src):
            n = self.mc["".join(q)]
            if n:
                for t in q:
                    self.credit[t] += n
        sset = set(src)
        self.scat = {"".join(q) for q in seqs(src)}
        self.ok = {t for q in seqs(md) if "".join(q) in sset for t in q}   # pieces of a source token

    def found(self, t):
        return self.mc[t] + self.mcat[t] + self.credit[t]

    def respaced(self, t):
        """A Markdown token that is a re-spacing of source text."""
        return t in self.scat or t in self.ok


def is_key(t):
    return bool(re.search(r"[0-9_]", t)) or (len(t) >= 2 and t.isascii() and t.isupper())


def norm_label(s):
    s = s.replace("#quot;", '"').replace("<br>", "").replace("<br/>", "")
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s))


def parse_mermaid(body):
    """(labels text, edges [(from label, to label, directed)]) of a flowchart."""
    labels = {}

    def node(m):
        labels[m.group(1)] = m.group(2)
        return m.group(1)
    body = NODE_Q.sub(node, body)
    body = NODE_U.sub(node, body)
    body = re.sub(r":::[\w-]+", "", body)                  # a:::cls
    edges, arrow_labels = [], []
    for line in (st for l in body.splitlines() for st in l.split(";")):   # `a --> b; b --> c`
        line = line.strip()
        if not line or line.startswith("%%") or re.match(r"(flowchart|graph)\b", line) \
                or re.match(r"(classDef|class|style|linkStyle|subgraph|end)\b", line):
            continue
        parts = ARROW.split(line)
        # parts: node, arrow, label, node, arrow, label, node ...
        for i in range(0, len(parts) - 3, 3):
            arrow, lab = parts[i + 1], parts[i + 2]
            arrow_labels.append(lab or "")
            # `a & b --> c & d` is every pair
            for x in (n.strip() for n in parts[i].split("&")):
                for y in (n.strip() for n in parts[i + 3].split("&")):
                    if x and y:
                        edges.append((labels.get(x, x), labels.get(y, y), arrow not in ("---", "-.-", "==="), lab or ""))
    text = " ".join(list(labels.values()) + arrow_labels)
    return text, edges


def md_text(seg):
    seg = INTERP.sub(" ", seg)
    seg = VISION.sub(" ", seg)
    seg = MERMAID.sub(lambda m: " " + parse_mermaid(MM_VISION.sub("", m.group(1)))[0].replace("#quot;", '"') + " ", seg)
    seg = IMAGE.sub(" ", seg)
    seg = re.sub(r"<!--.*?-->", " ", seg, flags=re.S)
    seg = seg.replace("<br>", " ").replace("\\|", "|").replace("\\\\", "\\")
    seg = re.sub(r"(?m)^\|?\s*:?-{3,}.*$", " ", seg)
    seg = re.sub(r"(?m)^(```|~~~)\w*\s*$", " ", seg)
    seg = re.sub(r"(?m)^#{1,6}\s", " ", seg)
    return seg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("md")
    ap.add_argument("layout")
    ap.add_argument("--min-coverage", type=float, default=0.97)
    ap.add_argument("--max-extra", type=float, default=0.03)
    ap.add_argument("--report")
    a = ap.parse_args()

    recs = [json.loads(l) for l in open(a.layout, encoding="utf-8")]
    meta, recs = recs[0], recs[1:]
    want = meta["sheets"]
    src_text = {s: [] for s in want}     # what must be carried over
    alt_text = {s: [s] for s in want}    # also acceptable (raw values, sheet name)
    shape_label, src_edges = {}, {s: [] for s in want}
    for r in recs:
        if r["kind"] == "cell":
            src_text[r["sheet"]].append(r["text"])
            if "value" in r:
                alt_text[r["sheet"]].append(str(r["value"]))
        elif r["kind"] == "shape":
            shape_label[r["id"]] = r["text"]
            if "duplicate_of" not in r:          # a stacked copy is not required twice
                src_text[r["sheet"]].append(r["text"])
            else:
                alt_text[r["sheet"]].append(r["text"])
        elif r["kind"] == "edge":
            if r.get("label"):
                src_text[r["sheet"]].append(r["label"])
            if r["how"] != "dangling":
                src_edges[r["sheet"]].append(r)
    images = {r["name"]: r.get("image") for r in recs if r["kind"] == "sheet"}

    md = open(a.md, encoding="utf-8").read()
    fails, warns = [], []
    found = [m.group(1) for m in ANCHOR.finditer(md)]
    if found != want:
        fails.append(f"G1 anchors: expected {want}, got {found}")
    seg, pos = {}, [(m.group(1), m.start()) for m in ANCHOR.finditer(md)]
    for i, (s, p) in enumerate(pos):
        seg[s] = seg.get(s, "") + md[p:(pos[i + 1][1] if i + 1 < len(pos) else len(md))]

    per = {}
    for s in want:
        body = seg.get(s, "")
        nv = len(VISION.findall(INTERP.sub("", body))) + sum(len(MM_VISION.findall(m.group(1))) for m in MERMAID.finditer(VISION.sub("", INTERP.sub("", body))))
        if nv:
            warns.append(f"{s}: {nv} vision region(s) UNVERIFIED (read from the render)")
        md_toks = tokens(md_text(body))
        mine = Counter(md_toks)
        # ordered-list numbers ("5. ...") are Markdown markup: they may satisfy
        # the source but are never counted as invented
        inv = Counter(tokens(LIST_NO.sub(" ", md_text(body))))
        src_toks = tokens("\n".join(src_text[s]))
        src = Counter(src_toks)
        allsrc = src + Counter(tokens("\n".join(alt_text[s])))
        mt = Matcher(src_toks + tokens("\n".join(alt_text[s])), md_toks)
        got = mt.found
        missing = {t: n - got(t) for t, n in src.items() if is_key(t) and got(t) < n}
        total = sum(src.values())
        cov = sum(min(n, got(t)) for t, n in src.items()) / total if total else 1.0
        invented = sorted({t for t in inv if is_key(t) and allsrc[t] == 0 and not mt.respaced(t)})
        extra = sum(n - allsrc[t] for t, n in inv.items() if not is_key(t) and n > allsrc[t] and not mt.respaced(t))
        n_md = sum(inv.values())
        if missing:
            fails.append(f"G2 omission {s}: " + ", ".join(f"{t}x{n}" for t, n in list(missing.items())[:8])
                         + (f" (+{len(missing) - 8})" if len(missing) > 8 else ""))
        if cov < a.min_coverage:
            fails.append(f"G3 coverage {s}: {cov:.3f} < {a.min_coverage}")
        if invented:
            fails.append(f"G4 invention {s}: " + ", ".join(invented[:8]) + (f" (+{len(invented) - 8})" if len(invented) > 8 else ""))
        if n_md and extra / n_md > a.max_extra:
            fails.append(f"G4 invention {s}: {extra}/{n_md} tokens not in the source")

        # G5 edges
        known = set()
        for e in src_edges[s]:
            f_, t_ = norm_label(shape_label[e["from"]]), norm_label(shape_label[e["to"]])
            known.add((f_, t_))
            if not e["directed"]:
                known.add((t_, f_))
        drawn, n_mm = set(), 0
        nointerp = INTERP.sub("", body)
        vis_spans = [v.span() for v in VISION.finditer(nointerp)]
        for m in MERMAID.finditer(nointerp):
            n_mm += 1
            # a whole diagram inside <!-- vision --> is unverified like `%% vision` lines
            in_vis = any(a0 <= m.start() and m.end() <= a1 for a0, a1 in vis_spans)
            checked = "" if in_vis else MM_VISION.sub("", m.group(1))
            for f_, t_, _, _ in parse_mermaid(m.group(1))[1]:
                drawn.add((norm_label(f_), norm_label(t_)))
            for f_, t_, d, _ in parse_mermaid(checked)[1]:
                k = (norm_label(f_), norm_label(t_))
                if k not in known and (d or (k[1], k[0]) not in known):
                    fails.append(f"G5 edge {s}: '{f_}' -> '{t_}' joins no connected shapes in the source")
        if n_mm and src_edges[s]:
            lost = [e for e in src_edges[s]
                    if (norm_label(shape_label[e["from"]]), norm_label(shape_label[e["to"]])) not in drawn
                    and (e["directed"] or (norm_label(shape_label[e["to"]]), norm_label(shape_label[e["from"]])) not in drawn)]
            for e in lost[:8]:
                fails.append(f"G5 edge {s}: source edge '{shape_label[e['from']]}' -> '{shape_label[e['to']]}' "
                             f"({e['id']}) missing from the flowchart")
        per[s] = {"coverage": round(cov, 4), "missing": len(missing), "invented": len(invented),
                  "extra_tokens": extra, "mermaid": n_mm, "source_edges": len(src_edges[s])}

    base = os.path.dirname(os.path.abspath(a.md))
    for m in IMAGE.finditer(md):
        if not re.match(r"[a-z]+://", m.group(1)) and not os.path.exists(os.path.join(base, m.group(1))):
            fails.append(f"G6 asset missing: {m.group(1)}")

    status = "FAIL" if fails else ("WARN" if warns else "PASS")
    rep = {"status": status, "source": meta.get("source"), "sha256": meta.get("sha256"),
           "fails": fails, "warns": warns, "per_sheet": per}
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
    for x in fails:
        print("FAIL", x)
    for x in warns:
        print("WARN", x)
    cov = [v["coverage"] for v in per.values()]
    print(f"{status}: {len(want)} sheet(s), min coverage {min(cov) if cov else '-'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
