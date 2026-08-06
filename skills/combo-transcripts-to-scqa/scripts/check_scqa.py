#!/usr/bin/env python3
"""Gate the SCQA report produced from group-discussion transcripts.

Usage:
  check_scqa.py --report <report.md> --work <workdir>

8 gates, all deterministic. Any FAIL exits 1.

Design note — the gates exist to stop a *plausible-looking* report that is not
actually grounded in the transcripts. So every citation must name a page, and
every quote is matched against **that page only**: a real sentence taken from
p.20 and cited as p.1 is a fabricated citation, and is failed as such.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

REQUIRED_HEADINGS = [
    "## 0. 入力一覧",
    "## 1. Situation",
    "## 2. Complication",
    "## 3. Question",
    "## 4. Answer",
    "## 5. 論点マップ",
    "## 6. 根拠引用",
    "## Appendix A 未確定事項",
]

SCQA_HEADINGS = ["## 1. Situation", "## 2. Complication", "## 3. Question", "## 4. Answer"]
MIN_SECTION_CHARS = 100
MIN_QUOTE_CHARS = 8
VERDICTS = ("合意", "対立", "未決", "対立なし")
PLACEHOLDER_RE = re.compile(
    r"TODO|TBD|FIXME|XXX|＜要記入＞|<要記入>|\blorem\b|<[a-zA-Z0-9 _\-]{0,20}>|＜[^＞]{0,20}＞", re.I
)
# A citation must name a page: "[D1 p.4]" or a range "[D1 p.4-5]".
CITE_RE = re.compile(r"\[(D\d+)\s+p\.(\d+)(?:\s*-\s*(\d+))?\]")
# Any bracketed doc reference, page or not — used to catch page-less "[D1]".
ANY_REF_RE = re.compile(r"\[(D\d+)([^\]]*)\]")
QUOTE_RE = re.compile(r"「([^」]*)」|『([^』]*)』")

results: list[tuple[str, str, str]] = []  # (gate, PASS/FAIL, detail)


def gate(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, "PASS" if ok else "FAIL", detail))


def squash(text: str) -> str:
    """NFKC + drop every whitespace char — for quote matching within one page."""
    return re.sub(r"\s", "", unicodedata.normalize("NFKC", text))


def norm_lines(text: str) -> list[str]:
    """Line-wise normalization for the verbatim §0 comparison."""
    return [ln.strip() for ln in unicodedata.normalize("NFKC", text).split("\n") if ln.strip()]


def prose_chars(body: str) -> int:
    """Count of real prose chars: markdown scaffolding does not inflate a section."""
    t = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    t = re.sub(r"```.*?```", "", t, flags=re.S)
    t = "\n".join(ln for ln in t.split("\n") if not ln.strip().startswith("|"))
    t = CITE_RE.sub("", ANY_REF_RE.sub("", t))
    t = re.sub(r"^[#>\-*+\d.\s]+", "", t, flags=re.M)
    t = re.sub(r"[`*_~]", "", t)
    return len(squash(t))


def split_sections(md: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Return [(heading, body), ...] in document order, plus duplicated headings."""
    out: list[tuple[str, str]] = []
    cur: str | None = None
    buf: list[str] = []
    for line in md.split("\n"):
        if line.startswith("## "):
            if cur is not None:
                out.append((cur, "\n".join(buf)))
            cur = line.strip()
            buf = []
        else:
            buf.append(line)
    if cur is not None:
        out.append((cur, "\n".join(buf)))
    seen: set[str] = set()
    dup = [h for h, _ in out if h in seen or seen.add(h)]  # type: ignore[func-returns-value]
    return out, dup


def body_of(sections: list[tuple[str, str]], heading: str) -> str | None:
    """Exact-heading lookup — a heading is either the required one or it is not."""
    for h, b in sections:
        if h == heading:
            return b
    return None


def table_rows(body: str) -> list[list[str]]:
    """Parse markdown table rows into cells, dropping header separator rows."""
    rows = []
    for line in body.split("\n"):
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        rows.append(cells)
    return rows


def blockquote_blocks(body: str) -> list[tuple[int, str]]:
    """Merge consecutive '>' lines into one quote block: (line no, text)."""
    blocks: list[tuple[int, str]] = []
    cur: list[str] = []
    start = 0
    for n, line in enumerate(body.split("\n"), 1):
        if line.strip().startswith(">"):
            if not cur:
                start = n
            cur.append(re.sub(r"^\s*>\s?", "", line))
        elif cur:
            blocks.append((start, "\n".join(cur)))
            cur = []
    if cur:
        blocks.append((start, "\n".join(cur)))
    return blocks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--work", required=True)
    args = ap.parse_args()

    report = Path(args.report)
    work = Path(args.work)
    if not report.is_file():
        print(f"Error: report not found: {report}", file=sys.stderr)
        return 1
    manifest_path = work / "manifest.json"
    section0_path = work / "section0.md"
    if not manifest_path.is_file() or not section0_path.is_file():
        print(
            f"Error: run extract_transcripts.py first — missing {manifest_path} or "
            f"{section0_path}",
            file=sys.stderr,
        )
        return 1

    md = report.read_text(encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    docs = {d["id"]: d for d in manifest["docs"]}
    # per-page squashed text: a quote must live on the page it cites
    pages: dict[str, list[str]] = {}
    for d in manifest["docs"]:
        raw = (work / "text" / f"{d['id']}.txt").read_text(encoding="utf-8")
        pages[d["id"]] = [squash(p) for p in raw.split("\f")]
    sections, dup_headings = split_sections(md)

    # --- G0: the input inventory is the generated one, verbatim ---------------
    gen = section0_path.read_text(encoding="utf-8")
    gen_body = norm_lines(gen.split("\n", 1)[1] if "\n" in gen else "")
    sec0 = body_of(sections, "## 0. 入力一覧")
    g0_detail = ""
    if sec0 is None:
        g0_detail = "no exact '## 0. 入力一覧' heading"
    elif norm_lines(sec0) != gen_body:
        g0_detail = "§0 differs from work/section0.md — embed it verbatim, do not retype"
    gate("G0 入力一覧が生成物と一致(件数の手書き禁止)", not g0_detail, g0_detail)

    # --- G1: required headings, exact, in order, no duplicates ----------------
    present = [h for h, _ in sections]
    missing = [h for h in REQUIRED_HEADINGS if h not in present]
    order = [present.index(h) for h in REQUIRED_HEADINGS if h in present]
    g1 = []
    if missing:
        g1.append(f"missing (exact match required): {missing}")
    if order != sorted(order):
        g1.append("out of order")
    if dup_headings:
        g1.append(f"duplicated: {dup_headings}")
    gate("G1 必須章立て(完全一致・順序・重複なし)", not g1, "; ".join(g1))

    # --- G2/G3: citations -----------------------------------------------------
    body_all = "\n".join(b for h, b in sections if h != "## 0. 入力一覧")
    cites = CITE_RE.findall(body_all)
    cited_ids = {c[0] for c in cites}
    uncited = sorted(set(docs) - cited_ids, key=lambda x: int(x[1:]))
    gate("G2 全入力PDFが最低1回は引用される", not uncited, f"uncited: {uncited}")

    bad = []
    for doc_id, tail in ANY_REF_RE.findall(body_all):
        if not re.fullmatch(r"\s+p\.\d+(\s*-\s*\d+)?", tail):
            bad.append(f"[{doc_id}{tail}] (page required: use [{doc_id} p.<n>])")
    for doc_id, p1, p2 in cites:
        if doc_id not in docs:
            bad.append(f"{doc_id} (unknown doc)")
            continue
        for p in (p1, p2):
            if p and not (1 <= int(p) <= docs[doc_id]["pages"]):
                bad.append(f"[{doc_id} p.{p}] (doc has {docs[doc_id]['pages']} pages)")
    gate("G3 引用はページ必須・ID/ページが実在", not bad, f"invalid: {sorted(set(bad))[:8]}")

    # --- G4: no placeholders outside Appendix A -------------------------------
    leaks = []
    for head, body in sections:
        if head.startswith("## Appendix A"):
            continue
        for n, line in enumerate(body.split("\n"), 1):
            if line.strip().startswith("<!--"):
                continue
            if PLACEHOLDER_RE.search(line):
                leaks.append(f"{head} L{n}: {line.strip()[:60]}")
    gate("G4 本文にplaceholder/未置換テンプレなし", not leaks, f"{leaks[:5]}")

    # --- G5: each SCQA section has real prose + citations ---------------------
    thin = []
    for heading in SCQA_HEADINGS:
        body = body_of(sections, heading)
        if body is None:
            thin.append(f"{heading}: missing")
            continue
        n = prose_chars(body)
        if n < MIN_SECTION_CHARS:
            thin.append(f"{heading}: {n} prose chars < {MIN_SECTION_CHARS}")
        if not CITE_RE.search(body):
            thin.append(f"{heading}: no [D<n> p.<n>] citation")
    q_body = body_of(sections, "## 3. Question")
    if q_body is not None and not re.search(r"[?？]|か。|か$", q_body, re.M):
        thin.append("## 3. Question: no interrogative sentence")
    gate("G5 S/C/Q/A が実体(散文100字)と引用を持つ", not thin, f"{thin[:6]}")

    # --- G6: 論点マップ is a real table; 対立 rows carry two sides -------------
    conflict_bad = []
    m_body = body_of(sections, "## 5. 論点マップ")
    conflicts = 0
    if m_body is None:
        conflict_bad.append("section missing")
    else:
        rows = table_rows(m_body)
        if len(rows) < 2:
            conflict_bad.append("no table rows")
        for r in rows[1:]:  # row 0 is the header
            if len(r) < 4:
                conflict_bad.append(f"needs 4 columns (論点|区分|立場|根拠): {'|'.join(r)[:60]}")
                continue
            verdict, stance, ev = r[1], r[2], r[3]
            if verdict not in VERDICTS:
                conflict_bad.append(f"区分 must be exactly one of {VERDICTS}: got '{verdict}'")
                continue
            if verdict != "対立":
                continue
            conflicts += 1
            ids = {c[0] for c in CITE_RE.findall(ev)}
            if len(ids) < 2:
                conflict_bad.append(f"対立 needs >=2 docs in 根拠: {'|'.join(r)[:60]}")
            stance_ids = set(re.findall(r"\bD\d+\b", stance))
            if len(stance_ids) < 2:
                conflict_bad.append(
                    f"対立 needs both sides named (e.g. 'D1: … / D2: …'): {stance[:50]}"
                )
        if conflicts == 0 and not any(r[1] == "対立なし" for r in rows[1:] if len(r) > 1):
            conflict_bad.append(
                "no 対立 row — if the sessions really agreed, say so with an explicit "
                "区分='対立なし' row rather than leaving the map without conflict"
            )
    gate("G6 論点マップの区分が厳密・対立は両論を明示", not conflict_bad, f"{conflict_bad[:5]}")

    # --- G7: every quote is verbatim on the page it cites ---------------------
    quote_bad = []
    quotes = 0
    q6 = body_of(sections, "## 6. 根拠引用")
    if q6 is None:
        quote_bad.append("section missing")
    else:
        for lineno, block in blockquote_blocks(q6):
            found = CITE_RE.findall(block)
            if len(found) != 1:
                quote_bad.append(
                    f"L{lineno}: one blockquote must carry exactly one [D<n> p.<n>] "
                    f"citation (found {len(found)})"
                )
                continue
            doc_id, p1, p2 = found[0]
            if doc_id not in pages:
                quote_bad.append(f"L{lineno}: unknown doc {doc_id}")
                continue
            lo = int(p1)
            hi = int(p2) if p2 else lo
            if not (1 <= lo <= hi <= len(pages[doc_id])):
                quote_bad.append(f"L{lineno}: page out of range for {doc_id}")
                continue
            hay = "".join(pages[doc_id][lo - 1 : hi])
            body_txt = CITE_RE.sub("", block)
            frags = [squash(a or b) for a, b in QUOTE_RE.findall(body_txt)]
            if not frags:
                frags = [squash(re.sub(r"[—–\-]\s*$", "", body_txt))]
            for frag in frags:
                quotes += 1
                if len(frag) < MIN_QUOTE_CHARS:
                    quote_bad.append(
                        f"L{lineno}: quote too short to be evidence "
                        f"({len(frag)} < {MIN_QUOTE_CHARS}): {frag[:20]}"
                    )
                elif frag not in hay:
                    quote_bad.append(
                        f"L{lineno}: not verbatim on {doc_id} p.{p1}"
                        f"{'-' + p2 if p2 else ''}: {frag[:40]}"
                    )
        if quotes == 0:
            quote_bad.append("no quotes found (>= 1 blockquote per 対立/未決 論点 expected)")
    gate("G7 引用文が引用先ページに逐語で存在", not quote_bad, f"{quote_bad[:5]}")

    width = max(len(g) for g, _, _ in results)
    print(f"report : {report}")
    print(f"inputs : {len(docs)} PDF(s), quotes checked: {quotes}, 対立 rows: {conflicts}\n")
    for name, verdict, detail in results:
        print(f"[{verdict}] {name.ljust(width)}  {detail if verdict == 'FAIL' else ''}".rstrip())
    failed = [n for n, v, _ in results if v == "FAIL"]
    print(f"\n{len(results) - len(failed)}/{len(results)} gates passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
