#!/usr/bin/env python3
"""check_opinion.py — FIVE フレームワーク(Fact / Interpretation / Value / Expression)で
書いた「意見」の md を検査し、付録の生成物を作る。

subcommands:
  report  work/ledger.json から 付録D(裁定台帳) と 付録E(出典一覧) を機械生成する。
          **件数はここでしか作らない。**
  check   出力 md と work/ を 17 ゲートで検査する。exit 0 = 全 PASS。
          exit 1 = FAIL あり。exit 2 = 入力不備 (検査できない)。

usage:
  python3 check_opinion.py report --work <work>
  python3 check_opinion.py check --report <out.md> --work <work>
                                 [--inputs <a.md> ...] [--stance <stance.md>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from init_opinion import build_claims, norm  # noqa: E402

CID_RE = re.compile(r"\[(C\d+)\]")
TABLE_RE = re.compile(r"^\s*\|")
TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
BULLET_RE = re.compile(r"^\s*(?:[-*+]\s+|\d{1,2}[.)]\s+)")
COUNT_RE = re.compile(r"(\d+)\s*件")
PLACEHOLDER_RE = re.compile(r"〈[^〉]*〉|<[^<>\n]{1,60}>|TODO|FIXME|TBD")
TOKEN_RE = re.compile(r"[一-龥ァ-ヶー]{2,}|[A-Za-z][A-Za-z0-9_-]{2,}")
OPINION_WORDS = ("と思う", "と思われ", "だろう", "べきだ", "べきである", "べきだろ",
                 "気がする", "感じる", "感じた", "印象", "期待したい", "かもしれない",
                 "望ましい", "残念", "面白い", "ではないか")

VERIFIED = ("CONFIRMED", "CORRECTED", "PARTIAL", "PRIVATE_PRIMARY")
STATUSES = VERIFIED + ("UNVERIFIED",)
KINDS = ("fact", "interpretation", "value")

SECTIONS = [
    (0, "この意見の読み方"),
    (1, "F｜事実（裏取り済み）"),
    (2, "I｜解釈（事実に意味を与える）"),
    (3, "V｜価値基準（判断の軸）"),
    (4, "E｜表明（意見）"),
    (5, "付録A. 未確認・保留（UNVERIFIED）"),
    (6, "付録B. 反論と、この意見が変わる条件"),
    (7, "付録C. 入力と作成条件"),
    (8, "付録D. 裁定台帳"),
    (9, "付録E. 出典一覧"),
]

results: list[tuple[str, bool, list[str]]] = []


def die(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(2)


def gate(name: str, problems: list[str]) -> None:
    results.append((name, not problems, problems))


def squash(s: str) -> str:
    """全角半角と空白をつぶした比較用の文字列。"""
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", "", s)


def strip_ids_digits(s: str) -> str:
    return re.sub(r"\d+", "", re.sub(r"C\d+", "", squash(s)))


# ------------------------------------------------------------------ md parsing
def split_sections(md: str) -> list[dict]:
    """`## N. Title` 単位に切る。fenced code の中の # は見出しにしない。"""
    secs: list[dict] = []
    fence = False
    for line in md.split("\n"):
        if FENCE_RE.match(line):
            fence = not fence
        if not fence:
            m = re.match(r"^##\s+(\d+)\.\s*(.+?)\s*$", line)
            if m:
                secs.append({"num": int(m.group(1)), "title": m.group(2), "lines": []})
                continue
        if secs:
            secs[-1]["lines"].append(line)
    return secs


def subsections(lines: list[str]) -> list[dict]:
    """`### ` 単位に切る (見出し前の導入は title="" の 1 件目)。"""
    out = [{"title": "", "lines": []}]
    fence = False
    for line in lines:
        if FENCE_RE.match(line):
            fence = not fence
        if not fence and line.startswith("### "):
            out.append({"title": line[4:].strip(), "lines": []})
            continue
        out[-1]["lines"].append(line)
    if not out[0]["lines"] or not "".join(out[0]["lines"]).strip():
        out = out[1:] if len(out) > 1 else out
    return out


def units(lines: list[str]) -> list[str]:
    """本文の「1 記述」= 段落 / 箇条書き 1 行 / 表 1 行。"""
    out: list[str] = []
    para: list[str] = []
    fence = False
    for line in lines:
        if FENCE_RE.match(line):
            fence = not fence
            continue
        if fence:
            continue
        s = line.strip()
        if not s or s.startswith("<!--") or s.startswith("#"):
            if para:
                out.append(" ".join(para))
                para = []
            continue
        if TABLE_RE.match(s):
            if para:
                out.append(" ".join(para))
                para = []
            if not TABLE_SEP_RE.match(s):
                out.append(s)
            continue
        if BULLET_RE.match(s):
            if para:
                out.append(" ".join(para))
                para = []
            out.append(s)
            continue
        para.append(s)
    if para:
        out.append(" ".join(para))
    return out


def body_text(lines: list[str], prose_only: bool = False, no_table: bool = True) -> str:
    """文字数判定用のテキスト。prose_only なら箇条書き・表を除いた地の文だけ。"""
    keep: list[str] = []
    fence = False
    for line in lines:
        if FENCE_RE.match(line):
            fence = not fence
            continue
        s = line.strip()
        if fence or not s or s.startswith("<!--") or s.startswith("#"):
            continue
        if TABLE_RE.match(s):
            if no_table:
                continue
            keep.append(s)
            continue
        if prose_only and BULLET_RE.match(s):
            continue
        keep.append(BULLET_RE.sub("", s))
    return squash(" ".join(keep))


def table_ratio(lines: list[str]) -> float:
    total = len(body_text(lines, no_table=False))
    tbl = sum(len(squash(l)) for l in lines
              if TABLE_RE.match(l.strip()) and not TABLE_SEP_RE.match(l.strip()))
    return (tbl / total) if total else 0.0


def labelled(lines: list[str], label: str) -> list[str]:
    """`- <label>:` 形式の値を集める。"""
    out = []
    for u in units(lines):
        m = re.match(rf"^\s*[-*+]\s*\*{{0,2}}{re.escape(label)}\*{{0,2}}\s*[:：]\s*(.*)$", u)
        if m:
            out.append(m.group(1).strip())
    return out


def stance_tokens(stance: str) -> list[str]:
    toks = [t for t in TOKEN_RE.findall(unicodedata.normalize("NFKC", stance)) if len(t) >= 2]
    seen: list[str] = []
    for t in toks:
        if t not in seen:
            seen.append(t)
    return seen


# ------------------------------------------------------------------ generation
def gen_ledger_table(rows: list[dict]) -> str:
    n = {k: sum(1 for r in rows if r.get("kind") == k) for k in KINDS}
    st = {s: sum(1 for r in rows if r.get("kind") == "fact" and r.get("status") == s)
          for s in STATUSES}
    out = ["## 8. 付録D. 裁定台帳", "",
           f"- claim 総数: {len(rows)} 件 "
           f"（F 事実 {n['fact']} 件 / I 解釈 {n['interpretation']} 件 / V 価値基準 {n['value']} 件）",
           "- 事実の裏取り結果: " + " / ".join(f"{s} {st[s]} 件" for s in STATUSES),
           "",
           "| ID | 元の記述 | 区分 | 判定 | 言い直し・解釈・軸 | 判定理由 |",
           "| -- | -------- | ---- | ---- | ------------------ | -------- |"]
    for r in rows:
        kind = r.get("kind", "")
        if kind == "fact":
            main, why = r.get("restated", ""), r.get("status_rationale", "")
        elif kind == "interpretation":
            main = r.get("interpretation", "")
            why = "他の見方: " + r.get("alternative", "")
        else:
            main = r.get("axis", "")
            why = "由来: " + r.get("origin", "")
        cell = lambda s: str(s).replace("|", "\\|").replace("\n", " ")  # noqa: E731
        out.append(f"| {r['id']} | {cell(r['text'])} | {kind} | {r.get('status','-') or '-'} "
                   f"| {cell(main)} | {cell(why)} |")
    out += ["", "> 本節はスクリプト生成物 (`work/ledger_table.md`)。件数は手で書かない。"]
    return "\n".join(out) + "\n"


def gen_sources(rows: list[dict], ev: dict) -> str:
    out = ["## 9. 付録E. 出典一覧", ""]
    pub = [(r, s) for r in rows for s in (r.get("sources") or [])]
    priv = [(r, s) for r in rows for s in (r.get("private_sources") or [])]
    out += [f"- 公開出典: {len(pub)} 件 / 非公開の一次資料: {len(priv)} 件", "",
            "| ID | 出典 | 発行元 | 公開日 | 参照日 | 取得 | URL |",
            "| -- | ---- | ------ | ------ | ------ | ---- | --- |"]
    for r, s in pub:
        e = ev.get(s.get("url", ""), {})
        out.append(f"| {r['id']} | {s.get('title','')} | {s.get('publisher','')} "
                   f"| {s.get('date','')} | {s.get('accessed','')} "
                   f"| {e.get('status','-')} | {s.get('url','')} |")
    if priv:
        out += ["", "### 非公開の一次資料（公開情報では検証できない）", "",
                "| ID | 資料 | ページ | 機密区分 | 保持者 |",
                "| -- | ---- | ------ | -------- | ------ |"]
        for r, s in priv:
            out.append(f"| {r['id']} | {s.get('document','')} | {s.get('page','')} "
                       f"| {s.get('classification','')} | {s.get('holder','')} |")
    out += ["", "> 本節はスクリプト生成物 (`work/sources.md`)。件数は手で書かない。"]
    return "\n".join(out) + "\n"


def cmd_report(work: pathlib.Path) -> None:
    rows = load_rows(work)
    ev_p = work / "evidence.json"
    ev = json.loads(ev_p.read_text(encoding="utf-8")) if ev_p.is_file() else {}
    (work / "ledger_table.md").write_text(gen_ledger_table(rows), encoding="utf-8")
    (work / "sources.md").write_text(gen_sources(rows, ev), encoding="utf-8")
    print(f"OK: {work/'ledger_table.md'}\n    {work/'sources.md'}")


def load_rows(work: pathlib.Path) -> list[dict]:
    p = work / "ledger.json"
    if not p.is_file():
        die(f"{p} not found — init_opinion.py を先に回すこと")
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))["rows"]
    except Exception as e:
        die(f"ledger.json を読めない: {e}")
    if not rows:
        die("ledger.json に行が無い")
    return rows


# ---------------------------------------------------------------------- checks
def cmd_check(report: pathlib.Path, work: pathlib.Path,
              inputs: list[str], stance_arg: str) -> int:
    if not report.is_file():
        die(f"report not found: {report}")
    # HTML コメント (雛形の書き方メモ) は本文ではないので落としてから見る
    md = re.sub(r"(?s)<!--.*?-->", "", norm(report.read_text(encoding="utf-8")))
    rows = load_rows(work)
    mani_p = work / "manifest.json"
    if not mani_p.is_file():
        die(f"{mani_p} not found")
    mani = json.loads(mani_p.read_text(encoding="utf-8"))
    claims_p = work / "claims.json"
    if not claims_p.is_file():
        die(f"{claims_p} not found")
    claims_obj = json.loads(claims_p.read_text(encoding="utf-8"))

    by_id = {r["id"]: r for r in rows}
    facts = [r for r in rows if r.get("kind") == "fact"]
    verified = [r for r in facts if r.get("status") in VERIFIED]
    unver = [r for r in facts if r.get("status") == "UNVERIFIED"]
    interps = [r for r in rows if r.get("kind") == "interpretation"]
    values = [r for r in rows if r.get("kind") == "value"]

    secs = split_sections(md)
    sec = {s["num"]: s for s in secs}
    body = lambda n: sec[n]["lines"] if n in sec else []  # noqa: E731

    # --- G0 入力不変性 -----------------------------------------------------
    p: list[str] = []
    paths = [pathlib.Path(x) for x in (inputs or mani.get("inputs", []))]
    try:
        docs2, claims2 = build_claims(paths)
    except SystemExit:
        docs2, claims2 = [], []
        p.append("入力テキストを再セグメントできない（パスが manifest と食い違う／消えている）")
    if claims2:
        obj2 = {"docs": docs2, "claims": claims2}
        sha2 = hashlib.sha256(
            json.dumps(obj2, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        if sha2 != mani.get("claims_sha256"):
            p.append("claims_sha256 不一致 — 入力または claims.json が後から書き換えられている")
        if claims_obj.get("claims") != claims2:
            p.append("claims.json が入力からの再現結果と一致しない")
        idx = {c["id"]: c for c in claims2}
        if set(idx) != set(by_id):
            p.append(f"台帳の claim 集合が入力と違う（台帳 {len(by_id)} / 再現 {len(idx)}）")
        for cid, c in idx.items():
            r = by_id.get(cid)
            if r and (r.get("text") != c["text"] or r.get("context") != c["context"]
                      or r.get("doc") != c["doc"]):
                p.append(f"{cid}: 台帳の text/context/doc が入力と一致しない")
    stance_p = pathlib.Path(stance_arg) if stance_arg else pathlib.Path(mani.get("stance", ""))
    if stance_p.is_file():
        if hashlib.sha256(stance_p.read_bytes()).hexdigest() != mani.get("stance_raw_sha256"):
            p.append("立場ファイルが init 実行時から変わっている")
    else:
        p.append(f"立場ファイルが見つからない: {stance_p}")
    ws = (work / "stance.md")
    stance_text = ws.read_text(encoding="utf-8") if ws.is_file() else ""
    if stance_p.is_file() and squash(stance_text) != squash(norm(stance_p.read_text(encoding="utf-8"))):
        p.append("work/stance.md が元の立場ファイルと一致しない（work 側だけ盛られている）")
    gate("G0 入力不変性", p)

    # --- G1 台帳網羅 -------------------------------------------------------
    p = []
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        p.append("台帳に ID の重複がある")
    if len(rows) != mani.get("claims"):
        p.append(f"台帳 {len(rows)} 行 ≠ manifest の claim 数 {mani.get('claims')}（行を消している）")
    gate("G1 台帳網羅", p)

    # --- G2 未裁定 0 -------------------------------------------------------
    p = []
    for r in rows:
        cid, k = r["id"], r.get("kind")
        if k not in KINDS:
            p.append(f"{cid}: kind が未裁定または不正（{k!r}）")
            continue
        if len(squash(r.get("kind_rationale", ""))) < 15:
            p.append(f"{cid}: kind_rationale が 15 字未満")
        if k == "fact":
            if r.get("status") not in STATUSES:
                p.append(f"{cid}: status が未裁定または不正（{r.get('status')!r}）")
            if r.get("status") in VERIFIED and len(squash(r.get("restated", ""))) < 10:
                p.append(f"{cid}: restated が空/短すぎる")
            if len(squash(r.get("status_rationale", ""))) < 15:
                p.append(f"{cid}: status_rationale が 15 字未満")
        elif k == "interpretation":
            if len(squash(r.get("interpretation", ""))) < 60:
                p.append(f"{cid}: interpretation が 60 字未満")
            if len(squash(r.get("alternative", ""))) < 30:
                p.append(f"{cid}: alternative（他の見方）が 30 字未満")
        else:
            for f, n in (("axis", 4), ("origin", 20), ("tradeoff", 30)):
                if len(squash(r.get(f, ""))) < n:
                    p.append(f"{cid}: {f} が {n} 字未満")
    gate("G2 未裁定 0", p)

    # --- G3 restated が写経でない -----------------------------------------
    p = []
    for r in verified:
        if squash(r.get("restated", "")) == squash(r.get("text", "")):
            p.append(f"{r['id']}: restated が元の記述と同一（言い直していない）")
    gate("G3 事実の言い直し", p)

    # --- G4 出典の実体 -----------------------------------------------------
    p = []
    ev_p = work / "evidence.json"
    ev = json.loads(ev_p.read_text(encoding="utf-8")) if ev_p.is_file() else {}
    ev_dir = work / "evidence"
    for r in facts:
        srcs = r.get("sources") or []
        st = r.get("status")
        if st in ("CONFIRMED", "CORRECTED", "PARTIAL") and not srcs:
            p.append(f"{r['id']}: status={st} なのに公開出典が無い")
        for s in srcs:
            url = (s or {}).get("url", "")
            miss = [f for f in ("title", "url", "publisher", "date", "accessed", "quote")
                    if not str((s or {}).get(f, "")).strip()]
            if miss:
                p.append(f"{r['id']}: 出典の必須項目が空 {miss} ({url})")
                continue
            if not re.match(r"^\d{4}(-\d{2}){0,2}$", s["date"]):
                p.append(f"{r['id']}: date が YYYY[-MM[-DD]] でない: {s['date']}")
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", s["accessed"]):
                p.append(f"{r['id']}: accessed が YYYY-MM-DD でない: {s['accessed']}")
            e = ev.get(url)
            if not e:
                p.append(f"{r['id']}: 未取得の URL（fetch_sources.py を回すこと）: {url}")
                continue
            if e.get("status") != 200:
                p.append(f"{r['id']}: 取得に失敗した URL を出典にしている [{e.get('status')}] {url}")
                continue
            name = str(e.get("text_file", ""))
            if not name or "/" in name or ".." in name:
                p.append(f"{r['id']}: 証跡ファイル名が不正: {name!r}")
                continue
            f = ev_dir / name
            if not f.is_file():
                p.append(f"{r['id']}: 証跡テキストが無い: {f}")
                continue
            if hashlib.sha256(f.read_bytes()).hexdigest() != e.get("text_sha256"):
                p.append(f"{r['id']}: 証跡テキストが改変されている: {f}")
                continue
            q = squash(s["quote"])
            if len(q) < 10:
                p.append(f"{r['id']}: quote が 10 字未満")
            elif q not in squash(f.read_text(encoding="utf-8")):
                p.append(f"{r['id']}: quote がページ本文に存在しない: {url}")
    gate("G4 出典の実体", p)

    # --- G5 PRIVATE_PRIMARY / UNVERIFIED の証跡 ---------------------------
    p = []
    for r in facts:
        if r.get("status") == "PRIVATE_PRIMARY":
            ps = r.get("private_sources") or []
            if not ps:
                p.append(f"{r['id']}: PRIVATE_PRIMARY なのに private_sources が空")
            for s in ps:
                miss = [f for f in ("document", "page", "classification", "holder", "quote")
                        if not str((s or {}).get(f, "")).strip()]
                if miss:
                    p.append(f"{r['id']}: private_sources の必須項目が空 {miss}")
            if r.get("sources"):
                p.append(f"{r['id']}: PRIVATE_PRIMARY に公開 URL が付いている（裁定が矛盾）")
        if r.get("status") == "UNVERIFIED":
            sr = r.get("searched") or {}
            if not sr.get("queries") or not sr.get("domains"):
                p.append(f"{r['id']}: UNVERIFIED の探索証跡（queries / domains）が無い")
    gate("G5 未検証・非公開の証跡", p)

    # --- G6 判定根拠が行ごとに固有 ----------------------------------------
    p = []
    for field, targets in (("kind_rationale", rows), ("status_rationale", facts),
                           ("alternative", interps), ("origin", values)):
        seen: dict[str, str] = {}
        for r in targets:
            key = strip_ids_digits(r.get(field, ""))
            if not key:
                continue
            if key in seen:
                p.append(f"{r['id']}: {field} が {seen[key]} と実質同一（定型文の複製）")
            else:
                seen[key] = r["id"]
    gate("G6 判定根拠の固有性", p)

    # --- G7 解釈が事実に乗っているか --------------------------------------
    p = []
    vids = {r["id"] for r in verified}
    for r in interps:
        b = r.get("basis") or []
        if not b:
            p.append(f"{r['id']}: basis が空（どの事実に乗った解釈か不明）")
        for cid in b:
            if cid not in vids:
                p.append(f"{r['id']}: basis の {cid} が裏取り済み fact でない")
        if squash(r.get("interpretation", "")) == squash(r.get("alternative", "")):
            p.append(f"{r['id']}: interpretation と alternative が同一（対立解釈になっていない）")
    gate("G7 解釈の足場", p)

    # --- G8 価値基準が立場に紐づくか --------------------------------------
    p = []
    toks = stance_tokens(stance_text)
    for r in values:
        o = squash(r.get("origin", ""))
        if not any(squash(t) in o for t in toks):
            p.append(f"{r['id']}: origin が立場ファイルのどの語にも紐づかない（一般論の可能性）")
        if squash(r.get("tradeoff", "")) in o or not r.get("tradeoff"):
            p.append(f"{r['id']}: tradeoff が由来の言い換えで、捨てるものが書かれていない")
    if not values:
        p.append("価値基準（kind=value）が 1 件も無い — V が空では FIVE にならない")
    gate("G8 価値基準の由来", p)

    # --- G9 章立てと生成物の逐語一致 --------------------------------------
    p = []
    # 表題は NFKC 正規化して比べる（｜/（） と |/() の差は落とさない）
    got = [(s["num"], squash(s["title"])) for s in secs]
    want = [(n, squash(t)) for n, t in SECTIONS]
    if got != want:
        p.append("章立てが規定と違う（順序・番号・表題）")
        for exp, act in zip(SECTIONS, got + [(None, None)] * (len(SECTIONS) - len(got))):
            if (exp[0], squash(exp[1])) != act:
                p.append(f"  期待 `## {exp[0]}. {exp[1]}` / 実際 {act}")
    for num, fn in ((7, "section0.md"), (8, "ledger_table.md"), (9, "sources.md")):
        gp = work / fn
        if not gp.is_file():
            p.append(f"{fn} が無い（report サブコマンドを回すこと）")
            continue
        want = "\n".join(gp.read_text(encoding="utf-8").split("\n")[1:])
        if squash(want) != squash("\n".join(body(num))):
            p.append(f"§{num} が {fn} の逐語コピーでない（手で書き換えている）")
    gate("G9 章立て・生成物一致", p)

    # --- G10 §1 事実編 -----------------------------------------------------
    p = []
    s1 = body(1)
    us = units(s1)
    for r in verified:
        cid = r["id"]
        hit = [u for u in us if f"[{cid}]" in u]
        if not hit:
            p.append(f"{cid}: 裏取り済み fact が §1 本文に出ていない")
            continue
        if not any(squash(r["restated"]) in squash(u) for u in hit):
            p.append(f"{cid}: [{cid}] を置いた記述に restated が逐語で入っていない")
    for u in us:
        cids = CID_RE.findall(u)
        if len(cids) > 3:
            p.append(f"1 記述に CID が {len(cids)} 個（3 個まで）: {u[:40]}…")
        for cid in cids:
            r = by_id.get(cid)
            if not r:
                p.append(f"§1 に台帳外の CID: {cid}")
            elif r.get("kind") != "fact" or r.get("status") not in VERIFIED:
                p.append(f"§1 に事実でない CID: {cid}（kind={r.get('kind')} status={r.get('status')}）")
    for w in OPINION_WORDS:
        if w in "".join(s1):
            p.append(f"§1 に私見表現「{w}」— §2/§4 へ回すこと")
    if not [x for x in subsections(s1) if x["title"]]:
        p.append("§1 に `###` のテーマ見出しが無い")
    gate("G10 F: 事実編", p)

    # --- G11 §2 解釈編 -----------------------------------------------------
    p = []
    s2 = body(2)
    subs2 = [x for x in subsections(s2) if x["title"]]
    if not subs2:
        p.append("§2 に `###` の解釈項目が無い")
    for x in subs2:
        for label, minlen in (("事実", 0), ("解釈", 60), ("他の見方", 30)):
            got_v = labelled(x["lines"], label)
            if not got_v:
                p.append(f"§2「{x['title']}」に `- {label}:` が無い")
            elif len(squash(got_v[0])) < minlen:
                p.append(f"§2「{x['title']}」の {label} が {minlen} 字未満")
        for v in labelled(x["lines"], "事実"):
            cids = CID_RE.findall(v)
            if not cids:
                p.append(f"§2「{x['title']}」の 事実 に CID が無い")
            for cid in cids:
                if cid not in vids:
                    p.append(f"§2「{x['title']}」: 事実 に裏取り済み fact でない {cid}")
    all2 = squash("\n".join(s2))
    for r in interps:
        if f"[{r['id']}]" not in "".join(s2):
            p.append(f"{r['id']}: 解釈が §2 に出ていない")
        if squash(r.get("interpretation", "")) not in all2:
            p.append(f"{r['id']}: 台帳の interpretation が §2 に逐語で入っていない")
        if squash(r.get("alternative", "")) not in all2:
            p.append(f"{r['id']}: 台帳の alternative（他の見方）が §2 に逐語で入っていない")
    gate("G11 I: 解釈編", p)

    # --- G12 §3 価値基準編 -------------------------------------------------
    p = []
    s3 = body(3)
    subs3 = [x for x in subsections(s3) if x["title"]]
    if not subs3:
        p.append("§3 に `###` の価値基準項目が無い")
    for x in subs3:
        for label in ("軸", "由来", "トレードオフ"):
            if not labelled(x["lines"], label):
                p.append(f"§3「{x['title']}」に `- {label}:` が無い")
    all3 = squash("\n".join(s3))
    for r in values:
        if f"[{r['id']}]" not in "".join(s3):
            p.append(f"{r['id']}: 価値基準が §3 に出ていない")
        for f in ("axis", "origin", "tradeoff"):
            if squash(r.get(f, "")) not in all3:
                p.append(f"{r['id']}: 台帳の {f} が §3 に逐語で入っていない")
    gate("G12 V: 価値基準編", p)

    # --- G13 §4 表明 -------------------------------------------------------
    p = []
    s4 = body(4)
    req = {"意見": 40, "根拠": 0, "前提の価値基準": 0, "私はこうする": 30,
           "この意見が変わる条件": 30}
    vals = {}
    for label, minlen in req.items():
        got_v = labelled(s4, label)
        if not got_v:
            p.append(f"§4 に `- {label}:` が無い")
            continue
        vals[label] = got_v[0]
        if len(squash(got_v[0])) < minlen:
            p.append(f"§4 の {label} が {minlen} 字未満")
    if "根拠" in vals:
        cids = [c for c in CID_RE.findall(vals["根拠"])]
        ok = [c for c in cids if c in vids or c in {r["id"] for r in interps}]
        if len(set(ok)) < 2:
            p.append("§4 の 根拠 が事実/解釈の CID 2 件以上を指していない")
    if "前提の価値基準" in vals:
        vcids = {r["id"] for r in values}
        if not [c for c in CID_RE.findall(vals["前提の価値基準"]) if c in vcids]:
            p.append("§4 の 前提の価値基準 が §3 の CID を指していない")
    hits = {t for t in toks if squash(t) in squash("\n".join(s4))}
    if len(hits) < 2:
        p.append(f"§4 が立場に紐づいていない（立場の語のヒット {len(hits)} < 2）")
    gate("G13 E: 表明", p)

    # --- G14 付録A（未確認） ----------------------------------------------
    p = []
    s5 = body(5)
    rows5 = [u for u in units(s5) if TABLE_RE.match(u)]
    for r in unver:
        line = [u for u in rows5 if r["id"] in u]
        if not line:
            p.append(f"{r['id']}: UNVERIFIED が 付録A に無い")
            continue
        if len(line) > 1:
            p.append(f"{r['id']}: 付録A に複数行ある（1 行 1 件）")
        q = squash(line[0])
        if not any(squash(x) in q for x in (r.get("searched") or {}).get("queries", [])):
            p.append(f"{r['id']}: 付録A の「探した範囲」が台帳 searched と対応しない")
    for u in rows5:
        for cid in re.findall(r"\bC\d+\b", u):
            r = by_id.get(cid)
            if r and not (r.get("kind") == "fact" and r.get("status") == "UNVERIFIED"):
                p.append(f"付録A に UNVERIFIED でない {cid} が混ざっている")
    gate("G14 付録A: 未確認", p)

    # --- G15 付録B（反論） -------------------------------------------------
    p = []
    s6 = body(6)
    subs6 = [x for x in subsections(s6) if x["title"]]
    if len(subs6) < 2:
        p.append("付録B の反論が 2 件未満（自分に都合の良い反論だけを置いていないか）")
    seen_notes: dict[str, str] = {}
    for x in subs6:
        for label in ("反論", "応答"):
            got_v = labelled(x["lines"], label)
            if not got_v:
                p.append(f"付録B「{x['title']}」に `- {label}:` が無い")
                continue
            if len(squash(got_v[0])) < 40:
                p.append(f"付録B「{x['title']}」の {label} が 40 字未満")
            key = strip_ids_digits(got_v[0])
            if key in seen_notes:
                p.append(f"付録B「{x['title']}」の {label} が {seen_notes[key]} と実質同一")
            else:
                seen_notes[key] = x["title"]
    gate("G15 付録B: 反論", p)

    # --- G16 placeholder と件数の手書き -----------------------------------
    p = []
    for s in secs:
        if s["num"] in (7, 8, 9):
            continue
        for line in s["lines"]:
            t = line.strip()
            if t.startswith("<!--") or t.startswith(">"):
                continue
            m = PLACEHOLDER_RE.search(t)
            if m:
                p.append(f"§{s['num']}: 雛形/TODO が残っている: {m.group(0)}")
    allowed = {len(rows), len(facts), len(verified), len(unver), len(interps), len(values),
               len(subs2), len(subs3), len(subs6)}
    allowed |= {sum(1 for r in facts if r.get("status") == s) for s in STATUSES}
    allowed |= {len([s for r in rows for s in (r.get("sources") or [])]),
                len([s for r in rows for s in (r.get("private_sources") or [])])}
    for s in secs:
        if s["num"] in (7, 8, 9):
            continue
        for n in COUNT_RE.findall("\n".join(s["lines"])):
            if int(n) not in allowed:
                p.append(f"§{s['num']}: 手書きの件数 {n} 件 が台帳の集計と合わない")
    gate("G16 placeholder・件数", p)

    # --- G17 散文主体 ------------------------------------------------------
    p = []
    n0 = len(body_text(body(0), prose_only=True))
    if n0 < 120:
        p.append(f"§0 の地の文が {n0} 字（120 字以上）")
    n1 = len(body_text(s1))
    if n1 < 200:
        p.append(f"§1 が {n1} 字（200 字以上）")
    for x in [y for y in subsections(s1) if y["title"]]:
        n = len(body_text(x["lines"], prose_only=True))
        if n < 40:
            p.append(f"§1「{x['title']}」の地の文が {n} 字（40 字以上）")
    for x in subs2:
        n = len(body_text(x["lines"]))
        if n < 150:
            p.append(f"§2「{x['title']}」が {n} 字（150 字以上）")
    n4 = len(body_text(s4))
    if n4 < 150:
        p.append(f"§4 が {n4} 字（150 字以上）")
    for num in (0, 1, 2, 3, 4):
        rt = table_ratio(body(num))
        if rt > 0.4:
            p.append(f"§{num} の表比率 {rt:.0%}（40% 以下）— 本文は散文で書く")
    gate("G17 散文主体", p)

    # --- 出力 -------------------------------------------------------------
    bad = 0
    for name, ok, probs in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        for x in probs[:20]:
            print(f"       - {x}")
        if len(probs) > 20:
            print(f"       - ... 他 {len(probs)-20} 件")
        bad += 0 if ok else 1
    print(f"\n{'PASS' if bad == 0 else 'FAIL'}: {len(results)-bad}/{len(results)} gates")
    return 0 if bad == 0 else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--work", required=True)
    c = sub.add_parser("check")
    c.add_argument("--report", required=True)
    c.add_argument("--work", required=True)
    c.add_argument("--inputs", nargs="*", default=[])
    c.add_argument("--stance", default="")
    a = ap.parse_args()
    if a.cmd == "report":
        cmd_report(pathlib.Path(a.work))
    else:
        sys.exit(cmd_check(pathlib.Path(a.report), pathlib.Path(a.work), a.inputs, a.stance))


if __name__ == "__main__":
    main()
