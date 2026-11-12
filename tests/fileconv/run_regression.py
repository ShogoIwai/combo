#!/usr/bin/env python3
"""run_regression.py — regression checks for combo-fileconv-* over a local corpus.

Runs each converter on each corpus document into an output directory (never
next to the source) and checks the result against facts re-derived from the
source *independently of the converter code* (raw OOXML / poppler), so a bug
shared by converter and check cannot cancel out.

Usage:
    run_regression.py [--out DIR] [--only docx,xlsx,pdf,md] [--corpus FILE]

Exit status: 0 if no check FAILed (WARN does not fail), 1 otherwise.
The report is printed and written to <out>/report.md; every check (PASS
included) goes to <out>/checks.json.
"""
import argparse
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import zipfile
from xml.etree import ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
COMBO = os.path.dirname(os.path.dirname(HERE))
ROOT = os.path.dirname(COMBO)
# FILECONV_SKILLS points the run at another skills tree (e.g. an old revision)
SKILLS = os.environ.get("FILECONV_SKILLS") or os.path.join(COMBO, "skills")

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
SS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
PKG_R = "{http://schemas.openxmlformats.org/package/2006/relationships}"


class Result:
    def __init__(self, kind, path):
        self.kind, self.path, self.checks = kind, path, []

    def add(self, name, status, detail=""):
        self.checks.append((name, status, detail))

    def ok(self, name, cond, detail=""):
        self.add(name, "PASS" if cond else "FAIL", detail)

    @property
    def status(self):
        s = [c[1] for c in self.checks]
        return "FAIL" if "FAIL" in s else ("WARN" if "WARN" in s else "PASS")


TIMEOUT = int(os.environ.get("FILECONV_TIMEOUT", "600"))  # seconds per command


def run(cmd):
    """Run a command; a hang (e.g. a headless browser that never exits) is a
    FAIL after TIMEOUT seconds, not a stuck run. The whole process group is
    killed so no child (Chromium) is left behind."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         start_new_session=True)
    try:
        so, se = p.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        so, se = p.communicate()
        return 124, so, f"timeout after {TIMEOUT}s: {' '.join(cmd[:3])}\n" + se
    return p.returncode, so, se


def squash(s):
    return re.sub(r"\s+", "", s)


def md_unescape(s):
    return s.replace("<br>", "\n").replace("\\|", "|").replace("\\\\", "\\")


def table_blocks(md):
    """List of tables, each a list of row lines, from a Markdown text."""
    tables, cur = [], []
    for line in md.splitlines():
        if line.startswith("| "):
            cur.append(line)
        elif cur:
            tables.append(cur)
            cur = []
    if cur:
        tables.append(cur)
    return [t for t in tables if len(t) >= 2 and t[1].startswith("| ---")]


def unescaped_pipes(line):
    return len(re.findall(r"(?<!\\)\|", line.replace("\\\\", "")))


def check_tables(res, md):
    bad = []
    for ti, t in enumerate(table_blocks(md)):
        n = unescaped_pipes(t[0])
        for ri, row in enumerate(t):
            if unescaped_pipes(row) != n:
                bad.append(f"table{ti + 1}.row{ri}")
    res.ok("table-shape", not bad, f"{len(bad)} ragged row(s) {bad[:3]}" if bad else "")


def records_meta(res, path, src):
    try:
        with open(path, encoding="utf-8") as f:
            lines = [json.loads(l) for l in f]
    except Exception as e:
        res.add("records", "FAIL", f"unreadable: {e}")
        return []
    meta = lines[0] if lines else {}
    h = hashlib.sha256(open(src, "rb").read()).hexdigest()
    res.ok("records-meta", meta.get("type") == "meta" and meta.get("sha256") == h,
           f"rev={meta.get('extractor_rev')}")
    ids = [r.get("id") for r in lines[1:]]
    dup = len(ids) - len(set(ids))
    res.ok("records-ids-unique", dup == 0, f"{dup} duplicate id(s)" if dup else f"{len(ids)} records")
    return lines


# ------------------------------------------------------------------ docx
def _inside(el, parents, tags):
    a = parents.get(el)
    while a is not None:
        if a.tag in tags:
            return True
        a = parents.get(a)
    return False


def docx_facts(path):
    z = zipfile.ZipFile(path)
    root = ET.fromstring(z.read("word/document.xml"))
    parents = {c: p for p in root.iter() for c in p}
    names, based, olvl, default = {}, {}, {}, None
    if "word/styles.xml" in z.namelist():
        for s in ET.fromstring(z.read("word/styles.xml")).iter(W + "style"):
            sid = s.get(W + "styleId")
            n = s.find(W + "name")
            names[sid] = n.get(W + "val") if n is not None else ""
            b = s.find(W + "basedOn")
            based[sid] = b.get(W + "val") if b is not None else None
            o = s.find(W + "pPr/" + W + "outlineLvl")
            olvl[sid] = o.get(W + "val") if o is not None else None
            if s.get(W + "type") == "paragraph" and s.get(W + "default") in ("1", "true"):
                default = sid

    def level(p, sid):
        """Outline level 0-8, 9 = body text, None = unset: the paragraph's own,
        then the style chain's (Word's navigation-pane rule)."""
        o = p.find(W + "pPr/" + W + "outlineLvl")
        if o is not None:
            return int(o.get(W + "val"))
        seen = set()
        while sid and sid not in seen:
            seen.add(sid)
            if olvl.get(sid) is not None:
                return int(olvl[sid])
            sid = based.get(sid)
        return None
    body = root.find(W + "body")

    def text(p):
        out = []
        for el in p.iter():
            if el.tag == W + "t" and not _inside(el, parents, (W + "txbxContent", MC_FALLBACK, W + "del", W + "moveFrom")):
                out.append(el.text or "")
        return "".join(out)

    def blocks(c):
        for ch in c:
            if ch.tag in (W + "p", W + "tbl"):
                yield ch
            elif ch.tag == W + "sdt":
                sc = ch.find(W + "sdtContent")
                if sc is not None:
                    yield from blocks(sc)

    seq, toc, paras, order = [], [], [], []
    for b in blocks(body):
        if b.tag == W + "tbl":
            seq.append(("T", None))
            # first non-empty cell text (paragraphs only) stands for the table in the order check
            first = ""
            live = [tr for tr in b.iter(W + "tr") if tr.find(W + "trPr/" + W + "del") is None]
            for tc in (tc for tr in live for tc in tr.iter(W + "tc")):
                first = "".join(text(q) for q in tc.iter(W + "p")
                                if not _inside(q, parents, (W + "txbxContent",))).strip()
                if first:
                    break
            order.append(("T", squash(first)))
            continue
        ps = b.find(W + "pPr/" + W + "pStyle")
        sid = ps.get(W + "val") if ps is not None else default
        sname = names.get(sid, "") or ""
        t = text(b).strip()
        if not t:
            continue
        paras.append(t)
        order.append(("P", squash(t)))
        lv = level(b, sid)
        if re.match(r"toc\s+\d+$", sname, re.I):
            toc.append(squash(t))
        elif (lv is not None and 0 <= lv <= 8) or (lv is None and re.match(r"heading\s+\d+$", sname, re.I)):
            seq.append(("H", squash(t)))
    return {"seq": seq, "toc": toc, "paras": paras, "order": order,
            "tables": sum(1 for s in seq if s[0] == "T")}


def check_docx(res, src, out):
    md_path, rec = os.path.join(out, "out.md"), os.path.join(out, "records.jsonl")
    rc, so, se = run(["bash", os.path.join(SKILLS, "combo-fileconv-docx-to-md/convert.sh"),
                      src, md_path, "--records", rec])
    res.ok("exit", rc == 0 and "Traceback" not in se, se.strip()[-200:] if rc else "")
    if rc:
        return
    md = open(md_path, encoding="utf-8").read()
    f = docx_facts(src)
    tables = table_blocks(md)
    res.ok("table-count", len(tables) == f["tables"], f"md={len(tables)} source={f['tables']}")
    check_tables(res, md)

    heads = [squash(l.lstrip("#").strip()) for l in md.splitlines() if re.match(r"#{1,6} ", l)]
    hset = set(h for k, h in f["seq"] if k == "H")
    # document order: Heading-N paragraphs and tables, in source order
    md_seq, in_table = [], False
    for line in md.splitlines():
        if line.startswith("| "):
            if not in_table:
                md_seq.append(("T", None))
            in_table = True
            continue
        in_table = False
        m = re.match(r"#{1,6} (.*)", line)
        if m and squash(m.group(1)) in hset:
            md_seq.append(("H", squash(m.group(1))))
    res.ok("order", md_seq == f["seq"],
           "" if md_seq == f["seq"] else f"md={len(md_seq)} source={len(f['seq'])} first diff at "
           + str(next((i for i, (a, b) in enumerate(zip(md_seq, f['seq'])) if a != b), min(len(md_seq), len(f['seq'])))))
    # every non-empty body paragraph and every table, in source order, as a
    # left-to-right subsequence of the Markdown text (catches moved paragraphs)
    # Markdown markers ("## ", "- ", "> ", "|") only add characters, so the
    # source text of each block must still occur as a substring.
    flat_all, pos, bad = squash(md_unescape(md)), 0, None
    for i, (k, t) in enumerate(f["order"]):
        if not t:
            continue
        j = flat_all.find(t, pos)
        if j < 0:
            bad = (i, k, t[:30])
            break
        pos = j + len(t)
    res.ok("block-order", bad is None, f"block {bad[0]} ({bad[1]}) {bad[2]!r} not found after previous block" if bad else f"{len(f['order'])} blocks")
    toc_as_head = [t for t in f["toc"] if t in heads]
    res.ok("toc-not-heading", not toc_as_head, f"{len(toc_as_head)} TOC entr(ies) rendered as heading")

    flat = squash(md_unescape(md))
    missing = [p for p in f["paras"] if squash(p) not in flat]
    res.ok("paragraph-coverage", not missing,
           f"{len(missing)}/{len(f['paras'])} missing, e.g. {missing[0][:40]!r}" if missing else f"{len(f['paras'])} paragraphs")
    records_meta(res, rec, src)


# ------------------------------------------------------------------ xlsx
def xlsx_facts(path):
    z = zipfile.ZipFile(path)
    n = set(z.namelist())
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target") for r in
            ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).iter(PKG_R + "Relationship")}
    shared = []
    if "xl/sharedStrings.xml" in n:
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(SS + "si"):
            shared.append("".join(t.text or "" for t in si.iter(SS + "t")))
    sheets = []
    for s in wb.iter(SS + "sheet"):
        tgt = rels[s.get(R_ID)]
        part = tgt.lstrip("/") if tgt.startswith("/") else "xl/" + tgt
        x = ET.fromstring(z.read(part))
        cells = formulas = cached_nonempty = 0
        for c in x.iter(SS + "c"):
            v, fo, is_ = c.find(SS + "v"), c.find(SS + "f"), c.find(SS + "is")
            if fo is not None:
                formulas += 1
            val = None
            if v is not None and v.text is not None:
                val = shared[int(v.text)] if c.get("t") == "s" else v.text
            elif is_ is not None:
                val = "".join(t.text or "" for t in is_.iter(SS + "t"))
            # same policy as the extractor: a formula, or a non-empty value
            if fo is not None or (val is not None and val != ""):
                cells += 1
            if val is not None and str(val).strip() != "":
                cached_nonempty += 1
        merges = len(list(x.iter(SS + "mergeCell")))
        srels = part.rsplit("/", 1)[0] + "/_rels/" + part.rsplit("/", 1)[1] + ".rels"
        cparts, tparts = [], []
        if srels in n:
            for r in ET.fromstring(z.read(srels)).iter(PKG_R + "Relationship"):
                t = r.get("Type", "").rsplit("/", 1)[-1]
                tgt = os.path.normpath(os.path.join(os.path.dirname(part), r.get("Target"))).replace(os.sep, "/")
                if r.get("Target", "").startswith("/"):
                    tgt = r.get("Target").lstrip("/")
                (cparts if t == "comments" else tparts if t == "threadedComment" else []).append(tgt)
        sheets.append({"name": s.get("name"), "cells": cells, "formulas": formulas,
                       "nonempty": cached_nonempty, "merged": merges,
                       "comments": sum(len(list(ET.fromstring(z.read(p)).iter(SS + "comment"))) for p in cparts if p in n),
                       "threaded": len(tparts)})
    return sheets, sum(s["comments"] for s in sheets)


def check_xlsx(res, src, out):
    md_path, rec = os.path.join(out, "out.md"), os.path.join(out, "records.jsonl")
    rc, so, se = run(["bash", os.path.join(SKILLS, "combo-fileconv-xlsx-to-md/convert.sh"),
                      src, md_path, "--records", rec])
    res.ok("exit", rc == 0 and "Error:" not in se and "Traceback" not in se, se.strip()[-200:])
    if rc:
        return
    res.ok("no-unreadable-sheet", "could not read sheet" not in se)
    md = open(md_path, encoding="utf-8").read()
    sheets, comments = xlsx_facts(src)
    secs = set(re.findall(r"^## (.*)$", md, re.M))
    miss = [s["name"] for s in sheets if s["nonempty"] and s["name"] not in secs]
    res.ok("sheet-sections", not miss, f"missing {miss}" if miss else f"{len(secs)} sheet section(s)")
    check_tables(res, md)

    lines = records_meta(res, rec, src)
    if not lines:
        return
    st = lines[0].get("stats", {})
    by = {}
    for r in lines[1:]:
        if r.get("kind") == "cell" and ("formula" in r or r.get("value") not in (None, "")):
            by[r["sheet"]] = by.get(r["sheet"], 0) + 1
    # empty-string cells are kept by the extractor; source counts every <c> with content
    diff = [(s["name"], s["cells"], by.get(s["name"], 0)) for s in sheets if s["cells"] != by.get(s["name"], 0)]
    res.ok("records-cells", not diff, f"sheet/source/records {diff[:3]}" if diff else f"{sum(by.values())} cells")
    res.ok("records-formulas", st.get("formulas") == sum(s["formulas"] for s in sheets),
           f"records={st.get('formulas')} source={sum(s['formulas'] for s in sheets)}")
    res.ok("records-merged", st.get("merged") == sum(s["merged"] for s in sheets),
           f"records={st.get('merged')} source={sum(s['merged'] for s in sheets)}")
    res.ok("records-comments", st.get("comments") == comments, f"records={st.get('comments')} source={comments}")
    thr = sum(s["threaded"] for s in sheets)
    if thr:
        res.add("threaded-comments", "WARN", f"{thr} sheet(s) carry threaded comments; only their legacy copies are extracted")
    if st.get("uncached_formulas"):
        res.add("uncached-formulas", "WARN",
                f"{st['uncached_formulas']} formula(s) have no cached result; the Markdown shows them empty")


def check_xlsx_layout(res, src, out):
    """Layout mode tooling: prepare, then the gate must pass the deterministic
    draft and must fail targeted corruptions of it (a cell identifier deleted,
    an invented one added, a flowchart edge rewired)."""
    if src.lower().endswith(".xls"):
        return
    tool = os.path.join(SKILLS, "combo-fileconv-xlsx-to-md")
    work = os.path.join(out, "layout")
    rc, so, se = run(["python3", os.path.join(tool, "xlsx_prepare.py"), src, work])
    res.ok("layout-prepare", rc == 0, (se or so).strip()[-200:])
    if rc:
        return
    # visible sheets straight from workbook.xml, not from openpyxl
    wb = ET.fromstring(zipfile.ZipFile(src).read("xl/workbook.xml"))
    visible = [x.get("name") for x in wb.iter(SS + "sheet") if x.get("state", "visible") == "visible"]
    layout = os.path.join(work, "layout.jsonl")
    lines = [json.loads(l) for l in open(layout, encoding="utf-8")]
    sh_recs = [r for r in lines[1:] if r["kind"] == "sheet"]
    res.ok("layout-sheets", [r["name"] for r in sh_recs] == visible,
           f"routes {lines[0].get('routes')}")
    import shutil
    if shutil.which("soffice"):
        noimg = [r["name"] for r in sh_recs if not (r.get("image") and os.path.exists(os.path.join(work, r["image"])))]
        res.ok("layout-renders", not noimg, f"missing render for {noimg[:3]}" if noimg else f"{len(sh_recs)} render(s)")
    dangling = sum(1 for r in lines[1:] if r["kind"] == "edge" and r["how"] == "dangling")
    if dangling:
        res.add("layout-dangling-connectors", "WARN", f"{dangling} connector network(s) join no two shapes; read them from the render")

    draft = open(os.path.join(work, "draft.md"), encoding="utf-8").read()
    gate = os.path.join(tool, "xlsx_check.py")

    def gate_rc(text):
        path = os.path.join(work, "mut.md")
        open(path, "w", encoding="utf-8").write(text)
        return run(["python3", gate, path, layout])[0]
    res.ok("layout-gate-accepts-draft", gate_rc(draft) == 0, "the deterministic draft must pass")
    muts = []
    cells = [r["text"] for r in lines[1:] if r["kind"] == "cell"]
    toks = __import__("collections").Counter(t for c in cells for t in re.findall(r"[A-Za-z0-9_]+", c))
    cand = sorted(t for t, n in toks.items() if n == 1 and len(t) >= 4 and (re.search(r"[0-9_]", t) or t.isupper())
                  and len(re.findall(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(t), draft)) == 1)
    if cand:
        t = cand[len(cand) // 2]
        muts.append(("del " + t, re.sub(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(t), "", draft, count=1)))
    first = re.search(r"(?m)^<!-- sheet: .*-->$", draft)
    if first:
        muts.append(("add phantom", draft[:first.end()] + "\nREG_PHANTOM 0x5A5A_DEAD" + draft[first.end():]))
    m = re.search(r"(?m)^    (s\d+) --> (s\d+)$", draft)
    if m:
        block = draft[draft.rfind("```mermaid", 0, m.start()):draft.find("```", m.end())]
        nodes = re.findall(r"(?m)^    (s\d+)[\[\(\{]", block)
        linked = set(re.findall(r"(?m)^    %s --> (s\d+)$" % m.group(1), block)) | {m.group(1)}
        other = [n for n in nodes if n not in linked]
        if other:
            muts.append((f"rewire {m.group(1)}->{other[0]}",
                         draft[:m.start()] + f"    {m.group(1)} --> {other[0]}" + draft[m.end():]))
    if not muts:
        res.add("layout-gate-rejects-corruption", "SKIP", "nothing to corrupt")
        return
    missed = [k for k, text in muts if gate_rc(text) == 0]
    res.ok("layout-gate-rejects-corruption", not missed,
           f"{len(muts) - len(missed)}/{len(muts)} corruptions rejected" + (f"; missed {missed}" if missed else ""))


# ------------------------------------------------------------------ pdf
def check_pdf(res, src, out, opts):
    md_path = os.path.join(out, "out.md")
    rc, so, se = run(["bash", os.path.join(SKILLS, "combo-fileconv-pdf-to-md/convert.sh"),
                      src, md_path] + opts)
    res.ok("exit", rc == 0, se.strip()[-200:])
    if rc:
        return
    pages = int(re.search(r"^Pages:\s+(\d+)", run(["pdfinfo", src])[1], re.M).group(1))
    first, last = 1, pages
    if "--pages" in opts:
        first, last = map(int, opts[opts.index("--pages") + 1].split("-"))
    got = [int(x) for x in re.findall(r"^## Page (\d+)$", open(md_path, encoding="utf-8").read(), re.M)]
    res.ok("page-sections", got == list(range(first, last + 1)), f"{len(got)} section(s) for {first}-{last}")
    empty = open(md_path, encoding="utf-8").read().count("_(no extractable text on this page)_")
    if empty:
        res.add("pages-without-text", "WARN", f"{empty} page(s) have no text layer (candidates for vision/OCR)")
    check_pdf_structured(res, src, out, opts)


def check_pdf_structured(res, src, out, opts):
    """Structured-mode tooling: prepare, then the gate must pass the raw text as
    a trivially faithful conversion and must fail targeted corruptions of it."""
    tool = os.path.join(SKILLS, "combo-fileconv-pdf-to-md")
    work = os.path.join(out, "prepare")
    rc, so, se = run(["python3", os.path.join(tool, "pdf_prepare.py"), src, work] + opts)
    res.ok("prepare", rc == 0, (se or so).strip()[-200:])
    if rc:
        return
    lines = [json.loads(l) for l in open(os.path.join(work, "blocks.jsonl"), encoding="utf-8")]
    meta, pages = lines[0], [r for r in lines[1:] if r["kind"] == "page"]
    first, last = meta["pages"]
    res.ok("prepare-pages", [p["page"] for p in pages] == list(range(first, last + 1)),
           f"routes {meta.get('routes')}")
    noimg = [p["page"] for p in pages if p["route"] != "text" and not (p.get("image") and os.path.exists(os.path.join(work, p["image"])))]
    res.ok("prepare-renders", not noimg, f"missing render for pages {noimg[:5]}" if noimg else "")

    raw = open(os.path.join(work, "raw.md"), encoding="utf-8").read()
    triv = re.sub(r"(?m)^## Page (\d+)$", r"<!-- page: \1 -->", raw)
    gate = os.path.join(tool, "pdf_check.py")
    blocks = os.path.join(work, "blocks.jsonl")

    def gate_rc(text, name):
        path = os.path.join(work, name)
        open(path, "w", encoding="utf-8").write(text)
        return run(["python3", gate, path, blocks])[0]

    res.ok("gate-accepts-raw", gate_rc(triv, "triv.md") == 0, "raw text-layer conversion must pass")
    # corruptions: drop a key token that occurs once on a checked page; add an invented one
    import random
    rnd = random.Random(0)
    checked = [p["page"] for p in pages if p["route"] != "vision"]
    rnd.shuffle(checked)
    caught, tried, missed, used = 0, 0, [], 0
    for p in checked:
        if used >= 3:
            break
        m = re.search(r"(?ms)^<!-- page: %d -->\n(.*?)(?=^<!-- page: |\Z)" % p, triv)
        body = m.group(1) if m else ""
        # candidates must be real tokens of the page's body blocks (not artefacts
        # of the -layout text such as "array2" for the source's "array 2")
        src_body = " ".join(r["text"] for r in lines[1:] if r["kind"] == "block" and r["page"] == p and r["role"] == "body")
        src_tok = set(re.findall(r"[A-Za-z0-9_]+", src_body))
        cand = [t for t, n in __import__("collections").Counter(re.findall(r"[A-Za-z0-9_]+", body)).items()
                if n == 1 and len(t) >= 4 and (re.search(r"[0-9_]", t) or t.isupper()) and triv.count(t) == 1
                and t in src_tok]
        if not cand:
            continue
        used += 1
        t = rnd.choice(cand)
        for kind, text in (("del " + t, re.sub(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(t), "", triv, count=1)),
                           ("add p%d" % p, triv.replace(f"<!-- page: {p} -->", f"<!-- page: {p} -->\nREG_PHANTOM_{p} 0x5A5A_DEAD", 1))):
            tried += 1
            if gate_rc(text, "mut.md") != 0:
                caught += 1
            else:
                missed.append(f"{kind} (p{p})")
    if tried == 0:
        res.add("gate-rejects-corruption", "SKIP", "no page carries a unique identifier to corrupt")
        return
    res.ok("gate-rejects-corruption", caught == tried, f"{caught}/{tried} corruptions rejected" + (f"; missed {missed}" if missed else ""))


# ------------------------------------------------------------------ md
def check_md(res, src, out):
    import shutil
    if not shutil.which("md-to-pdf"):
        res.add("md-to-pdf", "SKIP", "md-to-pdf not installed")
        return
    pdf = os.path.join(out, "out.pdf")
    rc, so, se = run(["bash", os.path.join(SKILLS, "combo-fileconv-md-to-pdf/convert.sh"), src, pdf])
    res.ok("exit", rc == 0, se.strip()[-200:])
    if rc:
        return
    res.ok("pdf-magic", open(pdf, "rb").read(5) == b"%PDF-")
    info = run(["pdfinfo", pdf])[1]
    pages = int(re.search(r"^Pages:\s+(\d+)", info, re.M).group(1))
    res.ok("pages", pages >= 1, f"{pages} page(s)")
    txt = squash(run(["pdftotext", pdf, "-"])[1])
    src_md = open(src, encoding="utf-8").read()
    heads = [squash(h) for h in re.findall(r"^#{1,6} (.+)$", src_md, re.M)][:30]
    miss = [h for h in heads if h and h not in txt]
    res.ok("headings-in-pdf", not miss, f"{len(miss)} heading(s) not found in PDF text" if miss else "")
    mer = src_md.count("```mermaid")
    if not mer:
        return
    if not shutil.which("mmdc"):
        res.add("mermaid", "WARN", f"{mer} mermaid block(s) are rendered as code (mmdc not installed for --mermaid)")
        return
    tool = os.path.join(SKILLS, "combo-fileconv-md-to-pdf")
    mpdf = os.path.join(out, "mermaid.pdf")
    rc, so, se = run(["bash", os.path.join(tool, "convert.sh"), "--mermaid", src, mpdf])
    res.ok("mermaid-exit", rc == 0, se.strip()[-200:])
    if rc:
        return
    mtxt = squash(run(["pdftotext", mpdf, "-"])[1])
    # independent of the pre-renderer: quoted labels straight from the Mermaid source
    blocks = re.findall(r"(?ms)^```mermaid\s*\n(.*?)^```", src_md)
    # word tokens, not whole labels: an SVG label's text may come out of
    # pdftotext interleaved with its neighbours
    tok = re.compile(r"[A-Za-z0-9_]+|[぀-ヿ㐀-鿿豈-﫿]")
    words = [t for b in blocks for l in re.findall(r'"([^"\n]+)"', b)
             for t in tok.findall(re.sub(r"<br\s*/?>", " ", l))]
    flat = "".join(tok.findall(mtxt))
    got = sum(1 for t in words if t in flat)
    res.ok("mermaid-labels", not words or got / len(words) >= 0.97, f"{got}/{len(words)} label words in the PDF text")
    res.ok("mermaid-not-code", "-->" not in mtxt and "flowchart" not in mtxt and "graphLR" not in mtxt,
           "diagram source text must not be printed")
    rc, so, se = run(["python3", os.path.join(tool, "md_pdf_qa.py"), src, mpdf, "--mermaid",
                      "--qa-dir", os.path.join(out, "qa")])
    res.ok("mermaid-qa", rc == 0, (so + se).strip()[-200:])
    # a broken block must fail loudly, naming its line
    bad = os.path.join(out, "broken.md")
    open(bad, "w", encoding="utf-8").write("# t\n\n```mermaid\nflowchart TD\n  A --> \n  ]]][[\n```\n")
    rc, so, se = run(["bash", os.path.join(tool, "convert.sh"), "--mermaid", bad, os.path.join(out, "broken.pdf")])
    res.ok("mermaid-rejects-broken", rc != 0 and "line 3" in se, (se.strip().splitlines() or [""])[-1][:160])


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "fileconv-regress"))
    ap.add_argument("--only", default="docx,xlsx,pdf,md")
    # the real corpus names internal documents, so it is local only (gitignored);
    # corpus.example.tsv shows the format
    ap.add_argument("--corpus", default=os.environ.get("FILECONV_CORPUS", os.path.join(HERE, "corpus.local.tsv")))
    a = ap.parse_args()
    kinds = set(a.only.split(","))
    results = []
    for line in open(a.corpus, encoding="utf-8"):
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.rstrip("\n").split("\t")
        kind, rel, opts = cols[0], cols[1], shlex.split(cols[2]) if len(cols) > 2 else []
        if kind not in kinds:
            continue
        src = os.path.join(ROOT, rel)
        res = Result(kind, rel)
        results.append(res)
        if not os.path.isfile(src):
            res.add("source", "FAIL", "not found")
            continue
        out = os.path.join(a.out, kind, re.sub(r"[^\w.-]+", "_", os.path.basename(rel)))
        os.makedirs(out, exist_ok=True)
        print(f"[{kind}] {rel}", file=sys.stderr)
        try:
            {"docx": lambda: check_docx(res, src, out), "xlsx": lambda: (check_xlsx(res, src, out), check_xlsx_layout(res, src, out)),
             "pdf": lambda: check_pdf(res, src, out, opts), "md": lambda: check_md(res, src, out)}[kind]()
        except Exception as e:
            res.add("harness", "FAIL", f"{type(e).__name__}: {e}")

    rep = ["# combo-fileconv regression report", "",
           "| kind | file | status | checks |", "| --- | --- | --- | --- |"]
    for r in results:
        notes = "<br>".join(f"{s} {n}{(': ' + d) if d else ''}".replace("|", "\\|") for n, s, d in r.checks if s != "PASS")
        rep.append(f"| {r.kind} | {os.path.basename(r.path)} | {r.status} | {notes or 'all PASS'} |")
    total = {s: sum(1 for r in results if r.status == s) for s in ("PASS", "WARN", "FAIL")}
    rep += ["", f"PASS {total['PASS']} / WARN {total['WARN']} / FAIL {total['FAIL']}"]
    text = "\n".join(rep) + "\n"
    os.makedirs(a.out, exist_ok=True)
    open(os.path.join(a.out, "report.md"), "w", encoding="utf-8").write(text)
    with open(os.path.join(a.out, "checks.json"), "w", encoding="utf-8") as f:   # every check, PASS included
        json.dump([{"kind": r.kind, "file": os.path.basename(r.path), "status": r.status,
                    "checks": [{"name": n, "status": st, "detail": d} for n, st, d in r.checks]} for r in results],
                  f, ensure_ascii=False, indent=1)
    print(text)
    sys.exit(1 if total["FAIL"] else 0)


if __name__ == "__main__":
    main()
