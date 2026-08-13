#!/usr/bin/env python3
"""check_opinion.py — FIVE フレームワーク(Fact / Interpretation / Value / Expression)で
書いた「意見」の md を検査し、付録の生成物を作る。

subcommands:
  report  work/ledger.json から 付録D(裁定台帳) と 付録E(出典一覧) を機械生成する。
          **件数はここでしか作らない。**
  check   出力 md と work/ を 19 ゲートで検査する。exit 0 = 全 PASS。
          exit 1 = FAIL あり。exit 2 = 入力不備 (検査できない)。

usage:
  python3 check_opinion.py report --work <work>
  python3 check_opinion.py check --report <out.md> --work <work>
                                 [--inputs <a.md> ...] [--stance <stance.md>]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import pathlib
import re
import sys
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from init_opinion import build_claims, norm, stance_anchors  # noqa: E402

CID_RE = re.compile(r"\[(C\d+)\]")
BARE_CID_RE = re.compile(r"\bC\d+\b")
TABLE_RE = re.compile(r"^\s*\|")
TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
BULLET_RE = re.compile(r"^\s*(?:[-*+]\s+|\d{1,2}[.)]\s+)")
COUNT_RE = re.compile(r"(\d+)\s*件")
# 雛形の取りこぼしを見る。ASCII の <…> は HTML タグ / autolink と紛れるので、
# 〈…〉 と既知の placeholder 語だけを対象にする (誤検出で散文を壊さない)。
PLACEHOLDER_RE = re.compile(r"〈[^〉]*〉|TODO|FIXME|TBD|\bXXX\b")
HTML_TAGISH_RE = re.compile(r"^(?:/?[a-zA-Z][a-zA-Z0-9-]*(?:\s[^<>]*)?/?|!--.*|https?://[^<>\s]+)$")
OPINION_WORDS = ("と思う", "と思われ", "だろう", "べきだ", "べきである", "べきだろ",
                 "気がする", "感じる", "感じた", "印象", "期待したい", "かもしれない",
                 "望ましい", "残念", "面白い", "ではないか")

# --- 逆分類の警戒シグナル (G18) --------------------------------------------
# 「これに当たったら分類禁止」ではなく「厚い説明責任を課す」ためのシグナル。
# 誤検出しても書けなくならないよう、罰は kind_rationale の字数だけにしてある。
FACTLIKE_RES = (
    re.compile(r"\d"),                                   # 数量・年・比率
    re.compile(r"[%％]|円|ドル|nm|GHz|MHz|TOPS|GB|TB|Gbps|W\b", re.I),
    re.compile(r"発表|公開|開始|終了|提供|発売|採用|搭載|対応|買収|提携|施行|成立|"
               r"出荷|量産|認定|受注|決定|導入|義務|禁止"),
    re.compile(r"によると|によれば|と述べ|と発表|と報じ|とされて"),
    re.compile(r"https?://"),
)
NORMATIVE_RES = (
    re.compile(r"べきだ|べきである|べきではない|すべき|望ましい|価値がある|大事だ|重要だ|"
               r"優先|許せない|受け入れられない|嫌だ|したくない|したい|好ましい|正しい|"
               r"間違っている|べく"),
)

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
    s = unicodedata.normalize("NFKC", str(s))
    return re.sub(r"\s+", "", s)


def strip_ids_digits(s: str) -> str:
    return re.sub(r"\d+", "", re.sub(r"C\d+", "", squash(s)))


# ------------------------------------------------------------------ md parsing
def scan_fence(lines: list[str]) -> list[bool]:
    """各行が fenced code block の内側かを返す。

    開始記号の**種類と長さ**を記憶し、同種で同じ長さ以上の閉じ記号だけで閉じる。
    (``` を ~~~ で閉じられると、以降の見出しを検査対象外に隠せてしまう)
    """
    out: list[bool] = []
    mark: str | None = None
    for line in lines:
        m = FENCE_RE.match(line)
        if mark is None:
            if m:
                mark = m.group(1)
                out.append(True)
            else:
                out.append(False)
            continue
        out.append(True)
        if m and m.group(1)[0] == mark[0] and len(m.group(1)) >= len(mark) \
                and not m.group(2).strip():
            mark = None
    return out


def strip_comments(md: str) -> str:
    """fence の**外側**の HTML コメントだけを落とす (書き方メモは本文ではない)。"""
    lines = md.split("\n")
    inside = scan_fence(lines)
    out: list[str] = []
    in_comment = False
    for line, fenced in zip(lines, inside):
        if fenced:
            out.append(line)
            continue
        buf = ""
        rest = line
        while rest:
            if in_comment:
                i = rest.find("-->")
                if i < 0:
                    rest = ""
                    break
                rest = rest[i + 3:]
                in_comment = False
            else:
                i = rest.find("<!--")
                if i < 0:
                    buf += rest
                    break
                buf += rest[:i]
                rest = rest[i + 4:]
                in_comment = True
        out.append(buf)
    return "\n".join(out)


def split_sections(md: str) -> list[dict]:
    """`## N. Title` 単位に切る。fenced code の中の # は見出しにしない。"""
    lines = md.split("\n")
    inside = scan_fence(lines)
    secs: list[dict] = []
    for line, fenced in zip(lines, inside):
        if not fenced:
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
    for line, fenced in zip(lines, scan_fence(lines)):
        if not fenced and line.startswith("### "):
            out.append({"title": line[4:].strip(), "lines": []})
            continue
        out[-1]["lines"].append(line)
    if not "".join(out[0]["lines"]).strip() and len(out) > 1:
        out = out[1:]
    return out


def units(lines: list[str]) -> list[str]:
    """本文の「1 記述」= 段落 / 箇条書き 1 行 / 表 1 行。"""
    out: list[str] = []
    para: list[str] = []
    for line, fenced in zip(lines, scan_fence(lines)):
        if fenced:
            continue
        s = line.strip()
        if not s or s.startswith("#"):
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
    for line, fenced in zip(lines, scan_fence(lines)):
        s = line.strip()
        if fenced or not s or s.startswith("#"):
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
    tbl = sum(len(squash(l)) for l, f in zip(lines, scan_fence(lines))
              if not f and TABLE_RE.match(l.strip()) and not TABLE_SEP_RE.match(l.strip()))
    return (tbl / total) if total else 0.0


def labelled(lines: list[str], label: str) -> list[str]:
    """`- <label>:` 形式の値を集める。"""
    out = []
    for u in units(lines):
        m = re.match(rf"^\s*[-*+]\s*\*{{0,2}}{re.escape(label)}\*{{0,2}}\s*[:：]\s*(.*)$", u)
        if m:
            out.append(m.group(1).strip())
    return out


def item_cids(x: dict) -> list[str]:
    """`###` 項目が担当する CID (見出しに置いた `[C#]`)。"""
    return CID_RE.findall(x["title"])


def valid_date(s: str) -> bool:
    """YYYY / YYYY-MM / YYYY-MM-DD を実在日として検査する。"""
    if not re.match(r"^\d{4}(-\d{2}){0,2}$", s):
        return False
    parts = [int(x) for x in s.split("-")]
    y = parts[0]
    if not (1900 <= y <= _dt.date.today().year + 1):
        return False
    try:
        _dt.date(y, parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except ValueError:
        return False
    return True


def matches(res, *texts: str) -> bool:
    t = " ".join(str(x) for x in texts)
    return any(r.search(t) for r in res)


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
            why = "他の見方: " + str(r.get("alternative", ""))
        else:
            main = r.get("axis", "")
            why = "由来: " + str(r.get("origin", ""))
        cell = lambda s: str(s).replace("|", "\\|").replace("\n", " ")  # noqa: E731
        out.append(f"| {r['id']} | {cell(r['text'])} | {kind} | {r.get('status','-') or '-'} "
                   f"| {cell(main)} | {cell(why)} |")
    out += ["", "> 本節はスクリプト生成物 (`work/ledger_table.md`)。件数は手で書かない。"]
    return "\n".join(out) + "\n"


def gen_sources(rows: list[dict], ev: dict) -> str:
    out = ["## 9. 付録E. 出典一覧", ""]
    pub = [(r, s) for r in rows for s in (r.get("sources") or []) if isinstance(s, dict)]
    priv = [(r, s) for r in rows for s in (r.get("private_sources") or []) if isinstance(s, dict)]
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


def load_rows(work: pathlib.Path) -> list[dict]:
    p = work / "ledger.json"
    if not p.is_file():
        die(f"{p} not found — init_opinion.py を先に回すこと")
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))["rows"]
    except Exception as e:
        die(f"ledger.json を読めない: {e}")
    if not isinstance(rows, list) or not rows:
        die("ledger.json の rows がリストでない/空")
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str):
            die("ledger.json の行が dict でない、または id が無い")
    return rows


def cmd_report(work: pathlib.Path) -> None:
    rows = load_rows(work)
    ev_p = work / "evidence.json"
    ev = json.loads(ev_p.read_text(encoding="utf-8")) if ev_p.is_file() else {}
    (work / "ledger_table.md").write_text(gen_ledger_table(rows), encoding="utf-8")
    (work / "sources.md").write_text(gen_sources(rows, ev), encoding="utf-8")
    print(f"OK: {work/'ledger_table.md'}\n    {work/'sources.md'}")


# ---------------------------------------------------------------------- checks
def check_types(rows: list[dict]) -> list[str]:
    """複合フィールドの型を先に固定する。

    型が想定外だと checker 自体が traceback で落ちる = FAIL ですらなくなるので、
    ここで問題として拾う。
    """
    p: list[str] = []
    for r in rows:
        cid = r["id"]
        for f in ("kind", "kind_rationale", "status", "restated", "status_rationale",
                  "interpretation", "alternative", "axis", "origin", "tradeoff",
                  "text", "context"):
            if not isinstance(r.get(f, ""), str):
                p.append(f"{cid}: {f} が文字列でない ({type(r.get(f)).__name__})")
        for f in ("sources", "private_sources", "basis"):
            v = r.get(f, [])
            if not isinstance(v, list):
                p.append(f"{cid}: {f} がリストでない ({type(v).__name__})")
                continue
            for e in v:
                if f == "basis":
                    if not isinstance(e, str):
                        p.append(f"{cid}: basis の要素が文字列でない")
                elif not isinstance(e, dict):
                    p.append(f"{cid}: {f} の要素が dict でない")
        sr = r.get("searched", {})
        if not isinstance(sr, dict):
            p.append(f"{cid}: searched が dict でない ({type(sr).__name__})")
        else:
            for f in ("queries", "domains"):
                v = sr.get(f, [])
                if v and (not isinstance(v, list) or not all(isinstance(x, str) for x in v)):
                    p.append(f"{cid}: searched.{f} が文字列リストでない")
    return p


def cmd_check(report: pathlib.Path, work: pathlib.Path,
              inputs: list[str], stance_arg: str) -> int:
    if not report.is_file():
        die(f"report not found: {report}")
    # HTML コメント (雛形の書き方メモ) は本文ではないので落としてから見る
    md = strip_comments(norm(report.read_text(encoding="utf-8")))
    rows = load_rows(work)
    mani_p = work / "manifest.json"
    if not mani_p.is_file():
        die(f"{mani_p} not found")
    mani = json.loads(mani_p.read_text(encoding="utf-8"))
    claims_p = work / "claims.json"
    if not claims_p.is_file():
        die(f"{claims_p} not found")
    claims_obj = json.loads(claims_p.read_text(encoding="utf-8"))

    type_problems = check_types(rows)
    if type_problems:
        # 型が壊れたまま先へ進むと traceback で検査そのものが死ぬ。ここで打ち切る。
        gate("G1 台帳の型と網羅", type_problems)
        for name, ok, probs in results:
            print(f"[{'PASS' if ok else 'FAIL'}] {name}")
            for x in probs[:20]:
                print(f"       - {x}")
        print("\nFAIL: 台帳の型が壊れているため以降のゲートを実行できない")
        return 1

    by_id = {r["id"]: r for r in rows}
    facts = [r for r in rows if r.get("kind") == "fact"]
    verified = [r for r in facts if r.get("status") in VERIFIED]
    unver = [r for r in facts if r.get("status") == "UNVERIFIED"]
    interps = [r for r in rows if r.get("kind") == "interpretation"]
    values = [r for r in rows if r.get("kind") == "value"]
    vids = {r["id"] for r in verified}

    secs = split_sections(md)
    sec = {s["num"]: s for s in secs}
    body = lambda n: sec[n]["lines"] if n in sec else []  # noqa: E731

    # --- G0 入力不変性 -----------------------------------------------------
    p: list[str] = []
    declared = inputs or mani.get("inputs", [])
    if not declared:
        p.append("入力パスが 1 つも無い（manifest の inputs が空 — 照合を空振りさせる改変）")
    paths = [pathlib.Path(x) for x in declared]
    docs2: list[dict] = []
    claims2: list[dict] = []
    if paths:
        try:
            docs2, claims2 = build_claims(paths)
        except SystemExit:
            p.append("入力テキストを再セグメントできない（パスが manifest と食い違う／消えている）")
    if claims2:
        obj2 = {"docs": docs2, "claims": claims2}
        sha2 = hashlib.sha256(
            json.dumps(obj2, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        if sha2 != mani.get("claims_sha256"):
            p.append("claims_sha256 不一致 — 入力または claims.json が後から書き換えられている")
        if claims_obj != obj2:
            p.append("claims.json が入力からの再現結果と一致しない（docs / claims の全項目照合）")
        if mani.get("claims") != len(claims2) or mani.get("docs") != len(docs2):
            p.append("manifest の件数が再現結果と一致しない")
        # ledger は claims と**同じ順序**で 1 対 1 に並んでいること
        if [r["id"] for r in rows] != [c["id"] for c in claims2]:
            p.append(f"台帳の行と順序が入力と違う（台帳 {len(rows)} / 再現 {len(claims2)}）")
        else:
            for r, c in zip(rows, claims2):
                if (r.get("text") != c["text"] or r.get("context") != c["context"]
                        or r.get("doc") != c["doc"]):
                    p.append(f"{r['id']}: 台帳の text/context/doc が入力と一致しない")
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
    gate("G1 台帳の型と網羅", p)

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
            url = str(s.get("url", ""))
            miss = [f for f in ("title", "url", "publisher", "date", "accessed", "quote")
                    if not str(s.get(f, "")).strip()]
            if miss:
                p.append(f"{r['id']}: 出典の必須項目が空 {miss} ({url})")
                continue
            if not re.match(r"^https?://[^\s<>\"']+$", url):
                p.append(f"{r['id']}: url が http(s) の絶対 URL でない: {url}")
                continue
            if not valid_date(str(s["date"])):
                p.append(f"{r['id']}: date が実在する YYYY[-MM[-DD]] でない: {s['date']}")
            if not (re.match(r"^\d{4}-\d{2}-\d{2}$", str(s["accessed"]))
                    and valid_date(str(s["accessed"]))):
                p.append(f"{r['id']}: accessed が実在する YYYY-MM-DD でない: {s['accessed']}")
            e = ev.get(url)
            if not e:
                p.append(f"{r['id']}: 未取得の URL（fetch_sources.py を回すこと）: {url}")
                continue
            if e.get("status") != 200:
                p.append(f"{r['id']}: 取得に失敗した URL を出典にしている [{e.get('status')}] {url}")
                continue
            for f in ("final_url", "fetched", "sha256"):
                if not str(e.get(f, "")).strip():
                    p.append(f"{r['id']}: evidence の {f} が空（取得記録として不完全）: {url}")
            # 証跡ファイル名は URL から決まる (fetch_sources.py の命名)。
            # ここを見ないと、別 URL の証跡を流用して未取得の URL を通せる。
            want_name = f"{hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]}.txt"
            name = str(e.get("text_file", ""))
            if name != want_name:
                p.append(f"{r['id']}: 証跡ファイル名が URL と対応しない"
                         f"（期待 {want_name} / 実際 {name!r}）— 別 URL の証跡の流用")
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
                        if not str(s.get(f, "")).strip()]
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
    anchors = stance_anchors(stance_text)
    if len(anchors) < 2:
        p.append("立場ファイルの『検査用アンカー』が 2 件未満 — V と E が立場固有かを検査できない")
    for r in values:
        o = squash(r.get("origin", ""))
        if anchors and not any(squash(a) in o for a in anchors):
            p.append(f"{r['id']}: origin が立場のアンカー（{'/'.join(anchors[:3])}…）に紐づかない")
        if squash(r.get("tradeoff", "")) in o or not r.get("tradeoff"):
            p.append(f"{r['id']}: tradeoff が由来の言い換えで、捨てるものが書かれていない")
    if not values:
        p.append("価値基準（kind=value）が 1 件も無い — V が空では FIVE にならない")
    gate("G8 価値基準の由来", p)

    # --- G9 章立てと生成物の逐語一致 --------------------------------------
    p = []
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
        wants = "\n".join(gp.read_text(encoding="utf-8").split("\n")[1:])
        if squash(wants) != squash("\n".join(body(num))):
            p.append(f"§{num} が {fn} の逐語コピーでない（手で書き換えている）")
    gate("G9 章立て・生成物一致", p)

    # --- G10 §1 事実編 -----------------------------------------------------
    p = []
    s1 = body(1)
    us = units(s1)
    placed: set[str] = set()
    for u in us:
        cids = CID_RE.findall(u)
        if len(cids) > 3:
            p.append(f"1 記述に CID が {len(cids)} 個（3 個まで）: {u[:40]}…")
        for cid in cids:
            r = by_id.get(cid)
            if not r:
                p.append(f"§1 に台帳外の CID: {cid}")
                continue
            if r.get("kind") != "fact" or r.get("status") not in VERIFIED:
                p.append(f"§1 に事実でない CID: {cid}（kind={r.get('kind')} status={r.get('status')}）")
                continue
            # その記述に置いた CID **全部**の restated が、その記述自身に入っていること
            if squash(r["restated"]) not in squash(u):
                p.append(f"§1: [{cid}] を置いた記述に {cid} の restated が入っていない: {u[:40]}…")
            else:
                placed.add(cid)
    for r in verified:
        if r["id"] not in placed:
            p.append(f"{r['id']}: 裏取り済み fact が §1 本文に（restated 逐語で）出ていない")
    for w in OPINION_WORDS:
        if w in "".join(s1):
            p.append(f"§1 に私見表現「{w}」— §2/§4 へ回すこと")
    if not [x for x in subsections(s1) if x["title"]]:
        p.append("§1 に `###` のテーマ見出しが無い")
    gate("G10 F: 事実編", p)

    # --- G11 §2 解釈編（項目 1 対 1） --------------------------------------
    p = []
    s2 = body(2)
    subs2 = [x for x in subsections(s2) if x["title"]]
    if not subs2:
        p.append("§2 に `###` の解釈項目が無い")
    covered: list[str] = []
    for x in subs2:
        cids = item_cids(x)
        if len(cids) != 1:
            p.append(f"§2「{x['title']}」の見出しに担当 CID が {len(cids)} 個（ちょうど 1 個）")
            continue
        cid = cids[0]
        covered.append(cid)
        r = by_id.get(cid)
        if not r or r.get("kind") != "interpretation":
            p.append(f"§2「{x['title']}」: {cid} は台帳の interpretation でない")
            continue
        blob = squash("\n".join(x["lines"]))
        for f, label in (("interpretation", "解釈"), ("alternative", "他の見方")):
            if squash(r.get(f, "")) not in blob:
                p.append(f"§2「{x['title']}」: 台帳の {f} がこの項目内に逐語で入っていない")
        for label, minlen in (("事実", 0), ("解釈", 60), ("他の見方", 30)):
            got_v = labelled(x["lines"], label)
            if not got_v:
                p.append(f"§2「{x['title']}」に `- {label}:` が無い")
            elif len(squash(got_v[0])) < minlen:
                p.append(f"§2「{x['title']}」の {label} が {minlen} 字未満")
        got_f = labelled(x["lines"], "事実")
        if got_f:
            shown = set(CID_RE.findall(got_f[0]))
            basis = set(r.get("basis") or [])
            if shown != basis:
                p.append(f"§2「{x['title']}」の 事実 {sorted(shown)} が台帳 basis "
                         f"{sorted(basis)} と一致しない")
    if len(covered) != len(set(covered)):
        p.append("§2 に同じ CID の項目が複数ある")
    missing = [r["id"] for r in interps if r["id"] not in set(covered)]
    if missing:
        p.append(f"§2 に出ていない解釈: {missing}")
    gate("G11 I: 解釈編", p)

    # --- G12 §3 価値基準編（項目 1 対 1） ----------------------------------
    p = []
    s3 = body(3)
    subs3 = [x for x in subsections(s3) if x["title"]]
    if not subs3:
        p.append("§3 に `###` の価値基準項目が無い")
    covered = []
    for x in subs3:
        cids = item_cids(x)
        if len(cids) != 1:
            p.append(f"§3「{x['title']}」の見出しに担当 CID が {len(cids)} 個（ちょうど 1 個）")
            continue
        cid = cids[0]
        covered.append(cid)
        r = by_id.get(cid)
        if not r or r.get("kind") != "value":
            p.append(f"§3「{x['title']}」: {cid} は台帳の value でない")
            continue
        blob = squash("\n".join(x["lines"]))
        for f in ("axis", "origin", "tradeoff"):
            if squash(r.get(f, "")) not in blob:
                p.append(f"§3「{x['title']}」: 台帳の {f} がこの項目内に逐語で入っていない")
        for label in ("軸", "由来", "トレードオフ"):
            if not labelled(x["lines"], label):
                p.append(f"§3「{x['title']}」に `- {label}:` が無い")
    if len(covered) != len(set(covered)):
        p.append("§3 に同じ CID の項目が複数ある")
    missing = [r["id"] for r in values if r["id"] not in set(covered)]
    if missing:
        p.append(f"§3 に出ていない価値基準: {missing}")
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
        cids = CID_RE.findall(vals["根拠"])
        ok = [c for c in cids if c in vids or c in {r["id"] for r in interps}]
        if len(set(ok)) < 2:
            p.append("§4 の 根拠 が事実/解釈の CID 2 件以上を指していない")
    if "前提の価値基準" in vals:
        vcids = {r["id"] for r in values}
        if not [c for c in CID_RE.findall(vals["前提の価値基準"]) if c in vcids]:
            p.append("§4 の 前提の価値基準 が §3 の CID を指していない")
    hits = {a for a in anchors if squash(a) in squash("\n".join(s4))}
    if len(hits) < 2:
        p.append(f"§4 が立場に紐づいていない（立場アンカーのヒット {len(hits)} < 2）")
    gate("G13 E: 表明", p)

    # --- G14 付録A（未確認） ----------------------------------------------
    p = []
    s5 = body(5)
    rows5 = [u for u in units(s5) if TABLE_RE.match(u)]
    seen5: list[str] = []
    for u in rows5:
        cids = BARE_CID_RE.findall(u)
        if not cids:
            continue  # ヘッダ行
        if len(cids) != 1:
            p.append(f"付録A の 1 行に CID が {len(cids)} 個（1 行 1 件）: {u[:40]}…")
            continue
        cid = cids[0]
        seen5.append(cid)
        r = by_id.get(cid)
        if not r:
            p.append(f"付録A に台帳外の CID: {cid}")
            continue
        if not (r.get("kind") == "fact" and r.get("status") == "UNVERIFIED"):
            p.append(f"付録A に UNVERIFIED でない {cid} が混ざっている")
            continue
        q = squash(u)
        sr = r.get("searched") or {}
        if not any(squash(x) in q for x in sr.get("queries", [])):
            p.append(f"{cid}: 付録A の「探した範囲」に台帳 searched.queries の語が無い")
        if not any(squash(x) in q for x in sr.get("domains", [])):
            p.append(f"{cid}: 付録A の「探した範囲」に台帳 searched.domains が無い")
    if len(seen5) != len(set(seen5)):
        p.append("付録A に同じ CID の行が複数ある")
    missing = [r["id"] for r in unver if r["id"] not in set(seen5)]
    if missing:
        p.append(f"付録A に出ていない UNVERIFIED: {missing}")
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
        for line, fenced in zip(s["lines"], scan_fence(s["lines"])):
            if fenced:
                continue
            t = line.strip()
            if t.startswith(">"):
                continue
            m = PLACEHOLDER_RE.search(t)
            if m:
                p.append(f"§{s['num']}: 雛形/TODO が残っている: {m.group(0)}")
            for tag in re.findall(r"<([^<>\n]{1,60})>", t):
                if not HTML_TAGISH_RE.match(tag.strip()):
                    p.append(f"§{s['num']}: 山括弧の雛形が残っている: <{tag}>")
    # 許される件数 = 台帳から機械的に出る集計 ∪ 根拠テキストに実在する「N 件」
    allowed = {len(rows), len(facts), len(verified), len(unver), len(interps), len(values),
               len(subs2), len(subs3), len(subs6)}
    allowed |= {sum(1 for r in facts if r.get("status") == s) for s in STATUSES}
    allowed |= {len([s for r in rows for s in (r.get("sources") or [])]),
                len([s for r in rows for s in (r.get("private_sources") or [])])}
    evidence_text = "\n".join(
        [str(r.get(f, "")) for r in rows
         for f in ("text", "restated", "interpretation", "alternative", "axis",
                   "origin", "tradeoff")]
        + [str(s.get("quote", "")) for r in rows for s in
           (r.get("sources") or []) + (r.get("private_sources") or [])])
    allowed |= {int(n) for n in COUNT_RE.findall(evidence_text)}
    for s in secs:
        if s["num"] in (7, 8, 9):
            continue
        for n in COUNT_RE.findall("\n".join(s["lines"])):
            if int(n) not in allowed:
                p.append(f"§{s['num']}: 手書きの件数 {n} 件 が台帳の集計にも根拠テキストにも無い")
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

    # --- G18 逆分類の説明責任 ----------------------------------------------
    # 意味の妥当性は機械では判定できない。ここでやるのは「表層シグナルのある
    # 逆分類には厚い説明を要求する」ことだけ。**分類の禁止ではない**ので、
    # 誤検出しても kind_rationale を 40 字書けば通る = 散文は壊れない。
    p = []
    for r in rows:
        k = r.get("kind")
        why = len(squash(r.get("kind_rationale", "")))
        if k in ("interpretation", "value") and matches(FACTLIKE_RES, r.get("text", "")):
            if why < 40:
                p.append(f"{r['id']}: 事実らしい記述（数値・日付・発表動詞・帰属表現）を "
                         f"{k} にしている。なぜ真偽を問える記述でないかを 40 字以上で"
                         f"（現在 {why} 字）")
        if k == "fact" and matches(NORMATIVE_RES, r.get("text", ""), r.get("restated", "")):
            if why < 40:
                p.append(f"{r['id']}: 規範的な語（べき・望ましい・優先 等）を含む記述を "
                         f"fact にしている。なぜ価値判断でなく事実かを 40 字以上で"
                         f"（現在 {why} 字）")
    gate("G18 逆分類の説明責任", p)

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
