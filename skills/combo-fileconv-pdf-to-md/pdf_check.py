#!/usr/bin/env python3
"""pdf_check.py — consistency gate for a structured PDF conversion.

Usage:
    pdf_check.py <structured.md> <blocks.jsonl> [--min-coverage 0.97] [--report out.json]

The structured Markdown must mark every page of the prepared range, in order,
with an anchor line `<!-- page: N -->` at the point where page N's content
starts. Model-written interpretation (figure descriptions, Mermaid redraws)
goes between `<!-- interpretation -->` and `<!-- /interpretation -->`, and
text read from a picture (absent from the text layer) between
`<!-- vision -->` and `<!-- /vision -->`. Both are excluded from every check,
so they can neither satisfy nor break them; vision regions are counted and
reported as unverified.

Checks (page window = the page and its neighbours, so a paragraph or table
joined across a page break still counts):
  G1 anchors      one anchor per page of the range, ascending, nothing else
  G2 omission     every key token (contains a digit or '_', or ALL-CAPS >= 2
                  letters) of a page's body blocks occurs in the Markdown
                  window at least as often as in the source page
  G3 coverage     share of the page's body tokens found in the window
                  >= --min-coverage (words; CJK characters count singly)
  G4 invention    every key token in a page's Markdown occurs somewhere in
                  the source window (header/footer blocks included), and the
                  page's other tokens exceed their source-window counts by at
                  most --max-extra of the page's Markdown tokens (editorial
                  notes such as "continued on the next page" belong in an
                  interpretation region)
  G5 assets       every image link resolves to an existing file
Pages routed `vision` have no text baseline: G2-G4 are skipped for them and
they are reported as UNVERIFIED (a WARN), never as passed.

These are consistency checks, not proof of fidelity: a value moved to the
wrong row with the same spelling passes. Exit 1 on any FAIL.
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter

ANCHOR = re.compile(r"^<!--\s*page:\s*(\d+)\s*-->\s*$", re.M)
INTERP = re.compile(r"<!--\s*interpretation\s*-->.*?<!--\s*/interpretation\s*-->", re.S)
VISION = re.compile(r"<!--\s*vision\s*-->.*?<!--\s*/vision\s*-->", re.S)
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)[^)]*\)")
LIST_NO = re.compile(r"(?m)^\s*\d+\.(?=\s)")
TOKEN = re.compile(r"[A-Za-z0-9_]+|[぀-ヿ㐀-鿿豈-﫿]")


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


def md_text(seg):
    seg = INTERP.sub(" ", seg)
    seg = VISION.sub(" ", seg)
    seg = IMAGE.sub(" ", seg)
    seg = re.sub(r"<!--.*?-->", " ", seg, flags=re.S)
    seg = seg.replace("<br>", " ").replace("\\|", "|").replace("\\\\", "\\")
    seg = re.sub(r"(?m)^\|?\s*:?-{3,}.*$", " ", seg)       # table separator rows
    seg = re.sub(r"(?m)^(```|~~~)\w*\s*$", " ", seg)        # fence lines (and their info string)
    return seg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("md")
    ap.add_argument("blocks")
    ap.add_argument("--min-coverage", type=float, default=0.97)
    ap.add_argument("--max-extra", type=float, default=0.03)
    ap.add_argument("--report")
    a = ap.parse_args()

    lines = [json.loads(l) for l in open(a.blocks, encoding="utf-8")]
    meta = lines[0]
    first, last = meta["pages"]
    route = {r["page"]: r["route"] for r in lines[1:] if r["kind"] == "page"}
    body, allsrc = {}, {}
    for r in lines[1:]:
        if r["kind"] != "block":
            continue
        allsrc.setdefault(r["page"], []).append(r["text"])
        if r["role"] == "body":
            body.setdefault(r["page"], []).append(r["text"])

    md = open(a.md, encoding="utf-8").read()
    fails, warns, notes = [], [], []

    # G1 anchors
    found = [int(m.group(1)) for m in ANCHOR.finditer(md)]
    want = list(range(first, last + 1))
    if found != want:
        fails.append(f"G1 anchors: expected pages {first}-{last} in order, got {found[:12]}{'...' if len(found) > 12 else ''}")
    seg = {}
    pos = [(int(m.group(1)), m.start()) for m in ANCHOR.finditer(md)]
    for i, (p, s) in enumerate(pos):
        e = pos[i + 1][1] if i + 1 < len(pos) else len(md)
        seg[p] = seg.get(p, "") + md[s:e]
    md_list = {p: tokens(md_text(t)) for p, t in seg.items()}
    # ordered-list numbers ("5. ...") are Markdown markup: never counted as invented
    md_inv = {p: Counter(tokens(LIST_NO.sub(" ", md_text(t)))) for p, t in seg.items()}
    md_tok = {p: Counter(v) for p, v in md_list.items()}
    src_list = {p: tokens("\n".join(allsrc.get(p, []))) for p in want}
    src_tok = {p: Counter(tokens("\n".join(body.get(p, [])))) for p in want}
    all_tok = {p: Counter(tokens("\n".join(allsrc.get(p, [])))) for p in want}

    def window(d, p):
        c = Counter()
        for q in (p - 1, p, p + 1):
            c.update(d.get(q, Counter()))
        return c

    per_page = {}
    for p in want:
        nv = len(VISION.findall(seg.get(p, "")))
        if nv and route.get(p) != "vision":
            warns.append(f"p{p}: {nv} vision region(s) UNVERIFIED (read from a picture)")
        if route.get(p) == "vision":
            warns.append(f"p{p}: UNVERIFIED (vision page, no text baseline)")
            per_page[p] = {"route": "vision", "status": "unverified"}
            continue
        sw, src = window(all_tok, p), src_tok[p]
        # window = the page and its neighbours; matching keeps token boundaries
        mt = Matcher([t for q in (p - 1, p, p + 1) for t in src_list.get(q, [])],
                     [t for q in (p - 1, p, p + 1) for t in md_list.get(q, [])])
        found = mt.found
        missing = {t: n - found(t) for t, n in src.items() if is_key(t) and found(t) < n}
        total = sum(src.values())
        covered = sum(min(n, found(t)) for t, n in src.items())
        cov = covered / total if total else 1.0
        own = Matcher([t for q in (p - 1, p, p + 1) for t in src_list.get(q, [])], md_list.get(p, []))
        invented = sorted({t for t in md_inv.get(p, Counter()) if is_key(t) and sw[t] == 0 and not own.respaced(t)})
        if missing:
            fails.append(f"G2 omission p{p}: " + ", ".join(f"{t}x{n}" for t, n in list(missing.items())[:8])
                         + (f" (+{len(missing) - 8})" if len(missing) > 8 else ""))
        if cov < a.min_coverage:
            fails.append(f"G3 coverage p{p}: {cov:.3f} < {a.min_coverage}")
        mine = md_inv.get(p, Counter())
        extra = sum(n - sw[t] for t, n in mine.items() if not is_key(t) and n > sw[t] and not own.respaced(t))
        n_md = sum(mine.values())
        if n_md and extra / n_md > a.max_extra:
            ex = [t for t, n in mine.items() if not is_key(t) and n > sw[t] and not own.respaced(t)][:8]
            fails.append(f"G4 invention p{p}: {extra}/{n_md} Markdown tokens not in the source window, e.g. {', '.join(ex)}")
        if invented:
            fails.append(f"G4 invention p{p}: " + ", ".join(invented[:8]) + (f" (+{len(invented) - 8})" if len(invented) > 8 else ""))
        per_page[p] = {"route": route.get(p), "coverage": round(cov, 4), "missing": len(missing),
                       "invented": len(invented), "extra_tokens": extra}

    # G5 assets
    base = os.path.dirname(os.path.abspath(a.md))
    for m in IMAGE.finditer(md):
        if not re.match(r"[a-z]+://", m.group(1)) and not os.path.exists(os.path.join(base, m.group(1))):
            fails.append(f"G5 asset missing: {m.group(1)}")

    status = "FAIL" if fails else ("WARN" if warns else "PASS")
    rep = {"status": status, "source": meta.get("source"), "sha256": meta.get("sha256"),
           "pages": [first, last], "fails": fails, "warns": warns, "per_page": per_page}
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
    for x in fails:
        print("FAIL", x)
    for x in warns:
        print("WARN", x)
    cov = [v["coverage"] for v in per_page.values() if "coverage" in v]
    print(f"{status}: pages {first}-{last}, checked {len(cov)}, unverified "
          f"{sum(1 for v in per_page.values() if v.get('status') == 'unverified')}, "
          f"min coverage {min(cov) if cov else '-'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
