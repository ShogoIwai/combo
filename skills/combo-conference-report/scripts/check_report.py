#!/usr/bin/env python3
"""check_report.py — カンファレンス参加レポートの機械生成ブロック作成 (report) と
ゲート検査 (check)。

  python3 check_report.py report --work <work>
      work/ledger.json から §1 判定サマリ / §6 裏取り台帳 / §7 出典一覧 を生成する。
      件数はここでしか作らない (本文に手で書かせないため)。

  python3 check_report.py check --report <out.md> --work <work>
                               [--notes a.md b.md] [--stance s.md]
      16 ゲート。exit 0 で PASS、1 で FAIL、2 で入力不備。
      --notes/--stance を渡すと manifest のパス申告を信用せず、その入力で再現照合する。

## 検査できることの限界 (正直に書いておく)

このスクリプトを走らせる主体は `work/` 配下を書ける。したがって
「入力メモも claims も manifest も evidence も全部つくり直して整合させる」改竄は
暗号学的には防げない。G0/G4 が守れるのは *部分的な* すり替え
(台帳だけ書き換える、証跡テキストだけ差し替える、取得していない URL を書く) までで、
全部を作り直す一貫改竄は検出できない。

だからこの skill は決定的ゲートだけに依存しない:
「出典が主張を意味的に裏づけるか」「fact/opinion の裁定が妥当か」「考察が立場固有か」は
**別モデルの反証レビュー結果 (work/review.json)** を G15 で必須の検査対象にしている。

ただし G15 が機械的に保証するのは *記録の網羅* だけである。review.json を書ける主体は
レポートを書いた主体と同じなので、「本当に別モデルに聞いたか」は保証できない
(信頼ベースの attestation)。独立性まで機械保証したいなら、別モデル側の実行系が
実行 ID つきの成果物を作成側が触れない場所に出し、それを検証する仕組みが必要になる。
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
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from init_ledger import build_claims, norm  # noqa: E402

PUBLIC_SOURCED = ("CONFIRMED", "CORRECTED", "PARTIAL")
PRIVATE = "PRIVATE_PRIMARY"          # 非公開の一次資料しか根拠が無い (公開URLでは裏取り不能)
BODY_STATUS = PUBLIC_SOURCED + (PRIVATE,)   # §2 に載せてよい / §4 の根拠にしてよい
FACT_STATUS = BODY_STATUS + ("UNVERIFIED",)
KINDS = ("fact", "opinion")

OPINION_MARKERS = [
    "と思う", "と思われる", "だろう", "ではないか", "べきだ", "べきである",
    "感じた", "印象", "期待したい", "個人的に", "私見", "気がする", "だと考える",
]
# 数値・日付・発表動詞を含む記述は fact 候補。opinion に逃がすなら理由を厚く要求する
FACTUAL_HINT = re.compile(
    r"(\d|発表|公開|提供|出荷|搭載|対応|リリース|買収|提携|価格|年内|四半期|世代|nm|GHz|TOPS|%)"
)
HINT_RATIONALE_MIN = 40
PLACEHOLDER = re.compile(r"\b(TODO|TBD|XXX|FIXME)\b|未記入|ここに記載|[<〈][^>〉\n]{1,30}[>〉]", re.I)
COUNT_RE = re.compile(r"[0-9０-９]+\s*件|[一二三四五六七八九十百数]+\s*件|件数")
CID_RE = re.compile(r"\[(C\d+(?:\s*[,、;；]\s*C\d+)*)\]")
DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
BAD_HOSTS = {"example.com", "example.org", "example.net", "localhost", "test.com"}
QUOTE_MIN = 10

SECTIONS = [
    (0, "入力と作成条件"),
    (1, "判定サマリ"),
    (2, "事実情報（公開情報で裏取り済み）"),
    (3, "私見・解釈の切り分け"),
    (4, "自組織メンバーへの考察"),
    (5, "未確認事項・要フォロー"),
    (6, "裏取り台帳"),
    (7, "出典一覧"),
]
GEN_FILES = {0: "section0.md", 1: "summary.md", 6: "ledger_table.md", 7: "sources.md"}


def die(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(2)


# ------------------------------------------------------------------ utilities
def load_json(p: pathlib.Path, required: bool = True) -> dict:
    if not p.is_file():
        if required:
            die(f"{p} not found — 先の手順を回すこと")
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        die(f"{p} を読めない: {e}")
    return {}


def cell(v) -> str:
    """Markdown 表セルの安全化 (| と改行とバックスラッシュを潰す)。"""
    s = str(v if v not in (None, "", [], {}) else "—")
    return s.replace("\\", "＼").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def cids(text: str) -> list[str]:
    out: list[str] = []
    for m in CID_RE.finditer(text):
        for c in re.split(r"[,、;；]", m.group(1)):
            c = c.strip()
            if c not in out:
                out.append(c)
    return out


PUNCT_RE = re.compile(r"[\s、。,.\-—・:：/()（）「」『』]+")


def key_of(s: str) -> str:
    """根拠文の重複判定キー: CID・数字・記号・空白を落とす。

    数字を落とすのは「定型文の末尾に ID や連番を足して重複判定を逃れる」手口を
    塞ぐため。**本文の照合には使わない** — 数値だけが違う 2 つの事実を同一視して
    しまうので、そちらは text_key()。
    """
    s = CID_RE.sub("", unicodedata.normalize("NFKC", str(s or "")))
    return PUNCT_RE.sub("", re.sub(r"C\d+|\d+", "", s))


def text_key(s: str) -> str:
    """本文照合キー: 記号と空白だけ落とし、**数値は保つ**（5W と 7W を区別する）。"""
    s = CID_RE.sub("", unicodedata.normalize("NFKC", str(s or "")))
    return PUNCT_RE.sub("", s)


def strip_noise(md: str) -> str:
    """コードフェンスと HTML コメントを落とす (そこに書いた CID を数えないため)。"""
    md = re.sub(r"(?s)<!--.*?-->", "", md)
    return re.sub(r"(?sm)^\s*(```|~~~).*?^\s*\1.*?$", "", md)


def rows_of(ledger: dict) -> list[dict]:
    rows = ledger.get("rows")
    if not isinstance(rows, list) or not rows:
        die("ledger.json の rows が不正 (list でないか空)")
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or not isinstance(r.get("id"), str):
            die(f"ledger.json rows[{i}] が不正 (dict / id が必要)")
    return rows


# ---------------------------------------------------------------- report mode
def do_report(work: pathlib.Path) -> None:
    rows = rows_of(load_json(work / "ledger.json"))
    facts = [r for r in rows if r.get("kind") == "fact"]
    opins = [r for r in rows if r.get("kind") == "opinion"]
    undecided = [r for r in rows if r.get("kind") not in KINDS]
    by = {s: [r for r in facts if r.get("status") == s] for s in FACT_STATUS}

    s = ["## 1. 判定サマリ", "",
         "| 区分 | 件数 |", "| ---- | ---- |",
         f"| メモから抽出した claim | {len(rows)} |",
         f"| うち一次情報(fact) | {len(facts)} |",
         f"| うち私見・解釈(opinion) | {len(opins)} |",
         f"| 未裁定 | {len(undecided)} |",
         "",
         "| fact の裏取り結果 | 件数 |", "| ----------------- | ---- |"]
    for st in FACT_STATUS:
        s.append(f"| {st} | {len(by[st])} |")
    s += ["", "> 本節はスクリプト生成物 (`work/summary.md`)。件数は手で書かない。",
          "> CONFIRMED=公開情報と一致 / CORRECTED=公開情報に照らして言い直した /",
          "> PARTIAL=一部のみ裏取り / PRIVATE_PRIMARY=非公開の一次資料のみが根拠 /",
          "> UNVERIFIED=公開情報が見つからず未確認。"]
    (work / "summary.md").write_text("\n".join(s) + "\n", encoding="utf-8")

    t = ["## 6. 裏取り台帳", "",
         "| ID | 区分 | 判定 | メモ上の記述 | 事実としての言い直し | 判定根拠 | 出典 |",
         "| -- | ---- | ---- | ------------ | -------------------- | -------- | ---- |"]
    for r in rows:
        pub = [x for x in (r.get("sources") or []) if isinstance(x, dict)]
        prv = [x for x in (r.get("private_sources") or []) if isinstance(x, dict)]
        src = " / ".join(f"[{x.get('title') or 'untitled'}]({x.get('url','')})" for x in pub)
        src += (" / " if src and prv else "") + " / ".join(
            f"{x.get('document','?')} p.{x.get('page','?')} (非公開)" for x in prv)
        t.append(
            f"| {cell(r.get('id'))} | {cell(r.get('kind'))} | {cell(r.get('status'))} "
            f"| {cell(r.get('text'))} | {cell(r.get('restated'))} "
            f"| {cell(r.get('status_rationale') or r.get('kind_rationale'))} | {cell(src or '—')} |"
        )
    t += ["", "> 本節はスクリプト生成物 (`work/ledger_table.md`)。1 claim = 1 行。"]
    (work / "ledger_table.md").write_text("\n".join(t) + "\n", encoding="utf-8")

    ev = load_json(work / "evidence.json", required=False)
    seen: dict[str, dict] = {}
    for r in rows:
        for x in r.get("sources") or []:
            if isinstance(x, dict) and x.get("url") and x["url"] not in seen:
                seen[x["url"]] = x
    u = ["## 7. 出典一覧", "",
         "| # | 出典 | 発行元 | 公開日 | 参照日 | 取得 | URL |",
         "| - | ---- | ------ | ------ | ------ | ---- | --- |"]
    for i, (url, x) in enumerate(seen.items(), 1):
        u.append(
            f"| S{i} | {cell(x.get('title'))} | {cell(x.get('publisher'))} | {cell(x.get('date'))} "
            f"| {cell(x.get('accessed'))} | {cell(ev.get(url, {}).get('status', '未取得'))} "
            f"| {cell(url)} |"
        )
    u += ["", "> 本節はスクリプト生成物 (`work/sources.md`)。取得列は fetch_sources.py の HTTP status。",
          "> 非公開の一次資料 (PRIVATE_PRIMARY) は URL を持たないので §6 の出典欄に出る。"]
    (work / "sources.md").write_text("\n".join(u) + "\n", encoding="utf-8")
    print(f"OK: summary.md / ledger_table.md / sources.md を生成 "
          f"({len(rows)} rows, {len(seen)} sources)")


# ----------------------------------------------------------------- check mode
def split_sections(md: str) -> tuple[dict[int, str], list[tuple[int, str]]]:
    secs: dict[int, str] = {}
    order: list[tuple[int, str]] = []
    cur: int | None = None
    buf: list[str] = []
    for line in md.split("\n"):
        m = re.match(r"^##\s*(\d+)\.\s*(.*)$", line)
        if m:
            if cur is not None:
                secs.setdefault(cur, "\n".join(buf))
            cur = int(m.group(1))
            order.append((cur, m.group(2).strip()))
            buf = []
            continue
        if cur is not None:
            buf.append(line)
    if cur is not None:
        secs.setdefault(cur, "\n".join(buf))
    return secs, order


def table_rows(body: str) -> list[list[str]]:
    out = []
    for line in body.split("\n"):
        s = line.strip()
        if not s.startswith("|") or re.match(r"^\|[\s:|-]+\|?$", s):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells and cells[0].lower() in ("id", "id(角括弧で書く)"):
            continue
        out.append(cells)
    return out


def do_check(report: pathlib.Path, work: pathlib.Path,
             notes_cli: list[str] | None, stance_cli: str | None) -> int:
    if not report.is_file():
        die(f"report not found: {report}")
    md_raw = report.read_text(encoding="utf-8")
    md = strip_noise(md_raw)
    claims_obj = load_json(work / "claims.json")
    ledger = load_json(work / "ledger.json")
    manifest = load_json(work / "manifest.json")
    rows = rows_of(ledger)
    byid: dict[str, dict] = {}
    dup_ids = []
    for r in rows:
        if r["id"] in byid:
            dup_ids.append(r["id"])
        byid[r["id"]] = r
    fails: list[str] = []

    def g(name: str, ok: bool, detail: str = "") -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
        if not ok:
            fails.append(name.split()[0])

    # --- G0 入力不変性 -------------------------------------------------------
    ok, detail = True, ""
    try:
        notes = [pathlib.Path(p) for p in (notes_cli or manifest.get("notes", []))]
        stance_p = pathlib.Path(stance_cli or manifest.get("stance", ""))
        if not notes:
            ok, detail = False, "入力メモが特定できない (manifest.notes も --notes も無い)"
        else:
            docs2, claims2 = build_claims(notes)
            if claims2 != claims_obj.get("claims") or docs2 != claims_obj.get("docs"):
                ok, detail = False, "claims.json を入力メモから再現できない (メモ改変 or claims 改竄)"
        if ok:
            recomputed = hashlib.sha256(
                json.dumps({"docs": docs2, "claims": claims2},
                           ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            if recomputed != manifest.get("claims_sha256"):
                ok, detail = False, "manifest.claims_sha256 が再計算値と不一致"
        if ok:
            want = [{k: c[k] for k in ("id", "doc", "context", "text")} for c in claims2]
            got = [{k: r.get(k) for k in ("id", "doc", "context", "text")} for r in rows]
            if want != got:
                diff = [w["id"] for w, h in zip(want, got) if w != h][:8]
                ok, detail = False, f"ledger の id/doc/context/text が claims と不一致: {diff}"
        if ok and (not stance_p.is_file() or hashlib.sha256(stance_p.read_bytes()).hexdigest()
                   != manifest.get("stance_raw_sha256")):
            ok, detail = False, "stance ファイルが消えた/変わった"
        if ok:
            # G13 が読む work/stance.md が、正本の stance と一致していること
            # (work 側だけに §4 で使った語を足して立場照合をすり抜けるのを防ぐ)
            wp = work / "stance.md"
            if not wp.is_file() or wp.read_text(encoding="utf-8").strip() != norm(
                    stance_p.read_bytes().decode("utf-8")).strip():
                ok, detail = False, "work/stance.md が正本の stance と一致しない"
    except SystemExit:
        ok, detail = False, "入力メモを読めない (移動/削除/重複)"
    except (OSError, UnicodeError, TypeError, KeyError, ValueError) as e:
        ok, detail = False, f"入力の再現に失敗: {type(e).__name__}: {e}"
    g("G0 入力不変性 (メモ→claims→ledger を再現・全項目照合、stance のハッシュ一致)", ok, detail)

    # --- G1 台帳網羅 ---------------------------------------------------------
    cl_ids = [c["id"] for c in claims_obj.get("claims", [])]
    g("G1 台帳網羅 (1 claim = 1 行、欠落・ID 重複なし)",
      [r["id"] for r in rows] == cl_ids and not dup_ids,
      f"claims={len(cl_ids)} ledger={len(rows)} dup={dup_ids} 差={sorted(set(cl_ids) ^ set(byid))[:8]}")

    # --- G2 未裁定 0 ---------------------------------------------------------
    bad = []
    for r in rows:
        if r.get("kind") not in KINDS:
            bad.append(f"{r['id']}:kind")
        if not str(r.get("kind_rationale") or "").strip():
            bad.append(f"{r['id']}:kind_rationale")
        if r.get("kind") == "fact":
            if r.get("status") not in FACT_STATUS:
                bad.append(f"{r['id']}:status")
            if not str(r.get("restated") or "").strip():
                bad.append(f"{r['id']}:restated")
            if not str(r.get("status_rationale") or "").strip():
                bad.append(f"{r['id']}:status_rationale")
    g("G2 未裁定 0 (kind/kind_rationale、fact は status/restated/status_rationale)", not bad, f"{bad[:10]}")

    # --- G3 restated がメモの写経でない -------------------------------------
    bad = [r["id"] for r in rows
           if r.get("kind") == "fact" and text_key(r.get("restated")) == text_key(r.get("text"))]
    g("G3 restated がメモの写経でない (裏取り後の言い直しになっている)", not bad, f"{bad[:10]}")

    # --- G4 公開出典の実体 + 取得証跡の完全性 --------------------------------
    ev = load_json(work / "evidence.json", required=False)
    ev_dir = work / "evidence"
    bad = []
    for r in rows:
        if r.get("status") not in PUBLIC_SOURCED:
            continue
        srcs = r.get("sources") or []
        if not isinstance(srcs, list) or not srcs:
            bad.append(f"{r['id']}:出典なし")
            continue
        for x in srcs:
            if not isinstance(x, dict):
                bad.append(f"{r['id']}:sources の型不正")
                continue
            u = str(x.get("url", ""))
            sp = urllib.parse.urlsplit(u)
            host = (sp.hostname or "").lower()
            if sp.scheme not in ("http", "https") or not host:
                bad.append(f"{r['id']}:url={u!r}")
                continue
            if host in BAD_HOSTS or any(host.endswith("." + h) for h in BAD_HOSTS):
                bad.append(f"{r['id']}:placeholder host {host}")
            for k in ("title", "publisher"):
                if not str(x.get(k, "")).strip():
                    bad.append(f"{r['id']}:{k} 空")
            if not DATE_RE.match(str(x.get("date", ""))):
                bad.append(f"{r['id']}:date={x.get('date')!r}")
            try:
                _dt.date.fromisoformat(str(x.get("accessed", "")))
            except ValueError:
                bad.append(f"{r['id']}:accessed={x.get('accessed')!r}")
            rec = ev.get(u)
            if not isinstance(rec, dict) or rec.get("status") != 200:
                bad.append(f"{r['id']}:未取得/取得失敗 ({(rec or {}).get('status', 'なし')}) {u}")
                continue
            name = str(rec.get("text_file", ""))
            want_name = f"{hashlib.sha256(u.encode('utf-8')).hexdigest()[:16]}.txt"
            if name != want_name:
                bad.append(f"{r['id']}:text_file が この URL の証跡名でない ({name!r} != {want_name})")
                continue
            tf = ev_dir / name
            if not tf.is_file():
                bad.append(f"{r['id']}:証跡テキストが無い {u}")
                continue
            if hashlib.sha256(tf.read_bytes()).hexdigest() != rec.get("text_sha256"):
                bad.append(f"{r['id']}:証跡テキストが text_sha256 と不一致 (手編集/差し替え)")
                continue
            for k in ("final_url", "fetched", "sha256"):
                if not str(rec.get(k, "")).strip():
                    bad.append(f"{r['id']}:evidence の {k} が無い")
            q = str(x.get("quote", "")).strip()
            if len(q) < QUOTE_MIN:
                bad.append(f"{r['id']}:quote が短すぎる/無い")
                continue
            page = re.sub(r"\s+", "", unicodedata.normalize("NFKC", tf.read_text(encoding="utf-8")))
            if re.sub(r"\s+", "", unicodedata.normalize("NFKC", q)) not in page:
                bad.append(f"{r['id']}:quote が取得ページに無い (捏造/転記ミス)")
    g("G4 公開出典の実体 (実取得 status=200・証跡ハッシュ一致・ページ内に実在する引用)",
      not bad, f"{bad[:8]}")

    # --- G5 非公開一次資料の証跡 --------------------------------------------
    bad = []
    for r in rows:
        if r.get("status") != PRIVATE:
            continue
        prv = r.get("private_sources")
        if not isinstance(prv, list) or not prv:
            bad.append(f"{r['id']}:private_sources なし")
            continue
        for x in prv:
            if not isinstance(x, dict):
                bad.append(f"{r['id']}:型不正")
                continue
            for k in ("document", "page", "classification", "quote", "holder"):
                if not str(x.get(k, "")).strip():
                    bad.append(f"{r['id']}:{k} 空")
        if r.get("sources"):
            bad.append(f"{r['id']}:公開 URL があるなら PRIVATE_PRIMARY ではない")
    g(f"G5 {PRIVATE} は資料名/ページ/機密区分/引用/保持者を持ち、公開 URL を持たない",
      not bad, f"{bad[:8]}")

    # --- G6 UNVERIFIED の探索証跡 -------------------------------------------
    bad = []
    for r in rows:
        if r.get("status") != "UNVERIFIED":
            continue
        s = r.get("searched")
        if not isinstance(s, dict) or not s.get("queries") or not s.get("domains"):
            bad.append(f"{r['id']}:searched.queries/domains が無い")
    g("G6 UNVERIFIED は探索証跡 (queries / domains) を持つ", not bad, f"{bad[:8]}")

    # --- G7 根拠文が行ごとに固有 --------------------------------------------
    dup: dict[str, list[str]] = {}
    for r in rows:
        for f in ("kind_rationale", "status_rationale"):
            k = key_of(r.get(f))
            if k:
                dup.setdefault(k, []).append(f"{r['id']}.{f}")
    same = [v for v in dup.values() if len(v) > 1]
    short = [r["id"] for r in rows if 0 < len(key_of(r.get("kind_rationale"))) < 12]
    g("G7 判定根拠が行ごとに固有 (定型文の複製・極端に短い根拠を禁止)",
      not same and not short, f"dup={same[:4]} short={short[:8]}")

    # --- G8 fact を opinion に逃がしていない --------------------------------
    bad = [f"{r['id']}(理由{len(key_of(r.get('kind_rationale')))}字)" for r in rows
           if r.get("kind") == "opinion" and FACTUAL_HINT.search(str(r.get("text", "")))
           and len(key_of(r.get("kind_rationale"))) < HINT_RATIONALE_MIN]
    g(f"G8 数値・日付・発表動詞を含む記述を opinion にするなら理由が厚い ({HINT_RATIONALE_MIN} 字以上)",
      not bad, f"{bad[:8]}")

    # --- G9 章立てと機械生成ブロック ----------------------------------------
    secs, order = split_sections(md_raw)
    normt = lambda s: unicodedata.normalize("NFKC", s).strip()
    ok = ([(n, normt(t)) for n, t in order] == [(n, normt(t)) for n, t in SECTIONS])
    g("G9a 章立て (§0〜§7 が正しい順・正確な表題で 1 回ずつ)", ok, f"{order}")
    for num, fname in GEN_FILES.items():
        p = work / fname
        if not p.is_file():
            g(f"G9b §{num} 機械生成ブロック ({fname})", False, "生成物が無い (report モード未実行)")
            continue
        body = "\n".join(p.read_text(encoding="utf-8").strip().split("\n")[1:]).strip()
        g(f"G9b §{num} 機械生成ブロック ({fname})", secs.get(num, "").strip() == body,
          "当該章の中身が生成物と一致しない (手で書き換えていないか)")

    # --- G10 §2 は裏取り済み fact を 1 項目ずつ・restated 逐語 --------------
    s2 = strip_noise(secs.get(2, ""))
    body_facts = [r for r in rows if r.get("kind") == "fact" and r.get("status") in BODY_STATUS]
    items2: list[tuple[list[str], str]] = []
    theme = False
    bad = []
    for line in s2.split("\n"):
        if re.match(r"^###\s+\S", line):
            theme = True
            continue
        if re.match(r"^\s*[-*]\s+\S", line):
            body = re.sub(r"^\s*[-*]\s+", "", line)
            ids = cids(line)
            if not theme:
                bad.append(f"テーマ見出しの前に項目がある: {body[:24]}")
            if len(ids) != 1:
                # CID 0 個 = 根拠のない記述の紛れ込み / 2 個以上 = 1 項目への詰め込み
                bad.append(f"項目の CID が {len(ids)} 個 (1 個であること): {body[:24]}")
                continue
            items2.append((ids, body))
        elif line.strip().startswith("|"):
            bad.append("§2 に表がある (箇条書きで書くこと)")
    if not theme:
        bad.append("### テーマ見出しが無い (台帳の再掲でなくテーマで整理する)")
    for r in body_facts:
        hit = [b for c, b in items2 if r["id"] in c]
        if len(hit) != 1:
            bad.append(f"{r['id']}: §2 の項目が {len(hit)} 個 (1 個であること)")
            continue
        if text_key(r.get("restated")) not in text_key(hit[0]):
            bad.append(f"{r['id']}: 項目本文が restated と一致しない")
    for c, _ in items2:
        for cid in c:
            if cid not in byid:
                bad.append(f"{cid}:台帳に無い")
            elif byid[cid].get("kind") != "fact" or byid[cid].get("status") not in BODY_STATUS:
                bad.append(f"{cid}:§2 に置けない区分/判定")
    marks = [m for m in OPINION_MARKERS if m in s2]
    g("G10 §2 はテーマ配下に裏取り済み fact が 1 項目ずつ・restated 逐語・私見表現なし",
      not bad and not marks, f"{bad[:6]} markers={marks}")

    # --- G11 §3 私見の切り分け ----------------------------------------------
    s3 = strip_noise(secs.get(3, ""))
    bad = []
    seen3: dict[str, list[str]] = {}
    sig3: dict[str, str] = {}
    for row in table_rows(s3):
        ids = cids(row[0]) if row else []
        if len(ids) != 1:
            bad.append(f"1 行に CID が {len(ids)} 個 ({row[0] if row else ''})")
            continue
        cid = ids[0]
        if cid in seen3:
            bad.append(f"{cid}:重複行")
        seen3[cid] = row
        if len(row) < 5 or any(not v.strip() or v.strip() in ("…", "-", "—") for v in row[1:5]):
            bad.append(f"{cid}:必須列が未記入")
            continue
        if text_key(row[1]) and text_key(row[1]) not in text_key(byid.get(cid, {}).get("text", "")):
            bad.append(f"{cid}:メモ上の私見列が台帳 text と対応しない")
        sig = text_key("".join(row[1:5]))
        if sig in sig3.values():
            bad.append(f"{cid}:他行と同じ作文の複製")
        sig3[cid] = sig
    op = {r["id"] for r in rows if r.get("kind") == "opinion"}
    miss = sorted(op - set(seen3), key=lambda x: int(x[1:]))
    extra = sorted(set(seen3) - op, key=lambda x: int(x[1:]))
    g("G11 §3 に opinion 全件、1 行 1 件、必須列が実質的に埋まる",
      not bad and not miss and not extra, f"{bad[:6]} 欠落={miss[:6]} 余分={extra[:6]}")

    # --- G12 §5 未確認の明示 ------------------------------------------------
    s5 = strip_noise(secs.get(5, ""))
    bad = []
    seen5: set[str] = set()
    for row in table_rows(s5):
        ids = cids(row[0]) if row else []
        if len(ids) != 1:
            bad.append(f"1 行に CID が {len(ids)} 個")
            continue
        cid = ids[0]
        if cid in seen5:
            bad.append(f"{cid}:重複行")
        seen5.add(cid)
        if len(row) < 4 or any(not v.strip() or v.strip() in ("…", "-", "—") for v in row[1:4]):
            bad.append(f"{cid}:必須列が未記入")
            continue
        s = byid.get(cid, {}).get("searched") or {}
        terms = [str(t) for t in (s.get("queries") or []) + (s.get("domains") or [])]
        if terms and not any(text_key(t) and text_key(t) in text_key(row[2]) for t in terms):
            bad.append(f"{cid}:探した範囲列が台帳 searched と対応しない")
    unv = {r["id"] for r in rows if r.get("status") == "UNVERIFIED"}
    miss = sorted(unv - seen5, key=lambda x: int(x[1:]))
    extra5 = sorted(seen5 - unv, key=lambda x: int(x[1:]))
    g("G12 §5 に UNVERIFIED 全件のみ、探した範囲が台帳 searched と対応",
      not bad and not miss and not extra5, f"{bad[:6]} 欠落={miss[:6]} 余分={extra5[:6]}")

    # --- G13 §4 考察の実体 --------------------------------------------------
    s4 = strip_noise(secs.get(4, ""))
    items = re.split(r"^###\s+", s4, flags=re.M)[1:]
    bad = []
    heads: list[str] = []
    if not items:
        bad.append("### の考察項目が 0")
    body_ids = {r["id"] for r in body_facts}
    for it in items:
        head = it.split("\n", 1)[0].strip()
        heads.append(head)
        f_ev = re.search(r"^\s*[-*]\s*根拠:\s*(.+)$", it, re.M)
        f_im = re.search(r"^\s*[-*]\s*示唆:\s*(.+)$", it, re.M)
        f_ac = re.search(r"^\s*[-*]\s*アクション:\s*(.+)$", it, re.M)
        if not f_ev or not cids(f_ev.group(1)):
            bad.append(f"{head}: 根拠 [C#] が無い")
        else:
            outside = sorted(set(cids(f_ev.group(1))) - body_ids)
            if outside:
                bad.append(f"{head}: 根拠が裏取り済み fact でない {outside}")
        if not f_im or len(key_of(f_im.group(1))) < 30:
            bad.append(f"{head}: 示唆が無い/薄い (30 字以上)")
        if not f_ac:
            bad.append(f"{head}: アクション行なし")
        else:
            a = f_ac.group(1)
            for k, minlen in (("担当", 2), ("内容", 8)):
                m = re.search(rf"{k}\s*[=:：]\s*([^/|]+)", a)
                if not m or len(m.group(1).strip()) < minlen:
                    bad.append(f"{head}: アクションの {k} が無い/薄い")
            m = re.search(r"期限\s*[=:：]\s*(\S+)", a)
            if not m:
                bad.append(f"{head}: アクションの期限が無い")
            else:
                try:
                    _dt.date.fromisoformat(m.group(1).strip("。."))
                except ValueError:
                    bad.append(f"{head}: 期限が ISO 日付でない ({m.group(1)})")
    stance = (work / "stance.md").read_text(encoding="utf-8") if (work / "stance.md").is_file() else ""
    # 日本語は空白で切れないので、漢字/カナ/英字の連続を語として拾う
    tokens = [t for t in re.findall(r"[一-龥]{2,}|[ァ-ヶー]{2,}|[A-Za-z]{3,}", stance)]
    if items and tokens and not any(t in s4 for t in tokens):
        bad.append("§4 が stance.md の語 (役割・所属・読み手) に一切触れていない")
    g("G13 §4 考察は 根拠(裏取り済み fact)+示唆+アクション(担当/内容/ISO 期限)、立場に紐づく",
      not bad, f"{bad[:6]}")

    # --- G14 placeholder / 件数の手書き -------------------------------------
    ph = sorted({m.group(0) for m in PLACEHOLDER.finditer(md)})
    g("G14a placeholder・雛形の山括弧が残っていない", not ph, f"{ph[:8]}")
    allowed: set[str] = set()
    for r in rows:
        srcs = [x for x in (r.get("sources") or []) + (r.get("private_sources") or [])
                if isinstance(x, dict)]
        for s in [str(r.get("restated", "")), str(r.get("text", ""))] + [
                str(x.get("quote", "")) for x in srcs]:
            allowed.update(text_key(c) for c in COUNT_RE.findall(s))
    hand = [c for n, body in secs.items() if n not in GEN_FILES
            for c in COUNT_RE.findall(strip_noise(body)) if text_key(c) not in allowed]
    g("G14b 件数の手書き禁止 (§2〜§5 の件数表現は台帳/引用に出るものだけ)", not hand, f"{hand[:8]}")

    # --- G15 反証レビュー記録 -----------------------------------------------
    rv = load_json(work / "review.json", required=False)
    bad = []
    if not rv:
        bad.append("work/review.json が無い (手順 8 の別モデル反証レビュー未実施)")
    else:
        if not str(rv.get("reviewer", "")).strip() or not str(rv.get("date", "")).strip():
            bad.append("reviewer / date が無い")
        facts_rv = rv.get("facts") or {}
        for r in body_facts:
            e = facts_rv.get(r["id"])
            if not isinstance(e, dict):
                bad.append(f"{r['id']}:出典が主張を裏づけるかのレビュー記録なし")
            elif e.get("supported") is not True or len(str(e.get("note", ""))) < 20:
                bad.append(f"{r['id']}:supported!=true か note が薄い")
        kinds_rv = rv.get("kinds") or {}
        for r in rows:
            if r.get("kind") == "opinion" and FACTUAL_HINT.search(str(r.get("text", ""))):
                e = kinds_rv.get(r["id"])
                if not isinstance(e, dict) or e.get("agree") is not True:
                    bad.append(f"{r['id']}:opinion 裁定の同意記録なし")
        st_rv = rv.get("stance_specific") or {}
        for h in heads:
            e = st_rv.get(h)
            if not isinstance(e, dict) or e.get("specific") is not True:
                bad.append(f"§4「{h}」:立場固有性のレビュー記録なし")
        notes = [str(e.get("note", "")) for e in
                 list(facts_rv.values()) + list(kinds_rv.values()) + list(st_rv.values())
                 if isinstance(e, dict)]
        keys = [key_of(n) for n in notes if key_of(n)]
        if keys and len(set(keys)) < len(keys):
            bad.append("レビュー note が複製されている (レビューしていない疑い)")
    g("G15 別モデルの反証レビュー記録 (work/review.json) が全対象を覆う", not bad, f"{bad[:6]}")

    print()
    if fails:
        print(f"RESULT: FAIL ({len(set(fails))} gate) -> {sorted(set(fails))}")
        return 1
    print("RESULT: PASS (all gates)")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("report")
    r.add_argument("--work", required=True)
    c = sub.add_parser("check")
    c.add_argument("--report", required=True)
    c.add_argument("--work", required=True)
    c.add_argument("--notes", nargs="+", help="manifest を信用せずこの入力で再現照合する")
    c.add_argument("--stance")
    a = ap.parse_args()
    if a.mode == "report":
        do_report(pathlib.Path(a.work))
    else:
        sys.exit(do_check(pathlib.Path(a.report), pathlib.Path(a.work), a.notes, a.stance))


if __name__ == "__main__":
    main()
