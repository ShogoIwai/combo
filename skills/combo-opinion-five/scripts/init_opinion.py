#!/usr/bin/env python3
"""init_opinion.py — 事実・解釈・価値観が混ざった自分のテキストを、決定的に
「主張(claim)単位」へ切り出し、FIVE フレームワークの裁定台帳の骨格と
付録C(§7 入力と作成条件) を起こす。

ここでは分類(F/I/V)も裏取りもしない。ここでやるのは
  * 入力の読み込みと正規化
  * claim への一意 ID 付与 (C1..Cn) — 以後の引用キー
  * 台帳骨格 work/ledger.json (全 claim ぶんの未裁定行)
  * work/stance.md (立場。未指定なら reference/stance_default.md)
  * work/section0.md (付録C。件数を人が書かないための機械生成物)
だけ。判断は LLM、集計と網羅は決定的スクリプト、という分担を崩さない。

セグメンテーションは check_opinion.py の G0 で**入力から再現・照合**される。
つまり claims.json / ledger.json の text を後から「裏取りしやすい主張」へ
すり替えることはできない。

usage:
  python3 init_opinion.py <input1.md> [<input2.txt> ...] [--stance <stance.md>]
                          --work <work> [--topic "<お題>"] [--overwrite]

--overwrite: 既存の run を上書きで作り直す（別サフィックスの run は作らない）。
  work の生成物（GENERATED）を消してから作り直す。消す前に入力と立場の検査を
  済ませるので、検査で止まったときは何も消えない。旧台帳の行のうち、新しい
  入力でも同じ入力ファイル (パスで照合) の中で本文 (context と text) が一字一句
  同じ claim は、裁定を出現順に 1 対 1 で引き継ぐ（basis の CID は新しい番号へ付け替え。根拠の claim が 1 つでも消えた・
  変わった解釈は引き継がず未裁定に戻して報告）。入力・立場ファイルが消す対象の
  中にあれば何も消さずに止める。
  本文が変わった claim は未裁定に戻る。証跡は消えるので fetch_sources.py から回し直す。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import unicodedata

BULLET_RE = re.compile(r"^\s*(?:[-*+]\s+|[・•]\s*|\(?\d{1,2}[.)）]\s*|[①-⑳]\s*)")
HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
QUOTE_RE = re.compile(r"^\s*>+\s?")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
INDENT_RE = re.compile(r"^(\s+|\t)")
SENT_SPLIT_RE = re.compile(r"(?<=[。．!?！？])\s*")
MIN_SENT = 25  # これ未満の断片は直前の claim へ寄せる (粒度が字数で揺れないように)

DEFAULT_STANCE = pathlib.Path(__file__).resolve().parent.parent / "reference" / "stance_default.md"

# init / fetch / report が work に書く生成物。--overwrite はこれだけを消す
# （入力・意見 md・利用者が work に置いた他のファイルには触れない）。
GENERATED = (
    "manifest.json", "claims.json", "ledger.json", "stance.md", "section0.md",
    "evidence.json", "evidence", "ledger_table.md", "sources.md",
)

LEDGER_FIELDS = {
    # --- 共通 -----------------------------------------------------------
    "kind": "",              # fact | interpretation | value
    "kind_rationale": "",    # なぜその kind か (行ごとに固有の文)
    # --- F: fact --------------------------------------------------------
    "status": "",            # CONFIRMED|CORRECTED|PARTIAL|PRIVATE_PRIMARY|UNVERIFIED
    "restated": "",          # 裏取り後の事実としての言い直し (§1 の本文はこれと逐語一致)
    "status_rationale": "",  # なぜその status か (行ごとに固有の文)
    "searched": {},          # UNVERIFIED のみ: {"queries": [...], "domains": [...]}
    "sources": [],           # 公開出典: [{"title","url","publisher","date","accessed","quote"}]
    "private_sources": [],   # 非公開の一次資料 (PRIVATE_PRIMARY のみ):
                             #   [{"document","page","classification","holder","quote"}]
    # --- I: interpretation ---------------------------------------------
    "basis": [],             # この解釈が乗っている裏取り済み fact の CID (1 つ以上)
    "interpretation": "",    # 事実に与えた意味 (60 字以上)
    "alternative": "",       # 同じ事実から出る**別の解釈** (30 字以上)
    # --- V: value -------------------------------------------------------
    "axis": "",              # 価値基準の名前 (何を良しとするか)
    "origin": "",            # その基準が自分のどこから来ているか (立場に紐づける)
    "tradeoff": "",          # その基準を採ると何を捨てるか (30 字以上)
}


ANCHOR_HEADING = "検査用アンカー"


def stance_anchors(stance: str) -> list[str]:
    """立場ファイルの `## 検査用アンカー` 配下の箇条書きを返す。

    ここを本文の自動抽出にすると「自分」「判断」「ファイル」のような一般語まで
    拾ってしまい、V(価値基準) と E(表明) が立場固有かの検査が空回りする。
    逆にひらがな中心の立場ファイルでは語が 1 つも取れず到達不能になる。
    だから**明示列挙**にして、init の時点で 2 件以上あることを保証する。
    """
    out: list[str] = []
    on = False
    for line in stance.split("\n"):
        if re.match(r"^\s*#{1,6}\s", line):
            on = ANCHOR_HEADING in line
            continue
        if on and BULLET_RE.match(line):
            v = BULLET_RE.sub("", line).strip().strip("`*_ ")
            if len(v) >= 2 and v not in out:
                out.append(v)
    return out


def die(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def norm(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFKC", text)
    return "\n".join(line.rstrip() for line in text.split("\n"))


def split_sentences(body: str) -> list[str]:
    parts = [p.strip() for p in SENT_SPLIT_RE.split(body) if p.strip()]
    if not parts:
        return []
    merged: list[str] = []
    for p in parts:
        if merged and len(p) < MIN_SENT:
            merged[-1] = f"{merged[-1]} {p}"
        else:
            merged.append(p)
    return merged


def segment(text: str) -> list[dict]:
    """行ベースの決定的セグメンテーション (check 側もこの関数で再現する)。

    * 見出し行は claim にせず、直近の見出しを context として持ち回る
    * fenced code block は丸ごと 1 claim
    * 表は区切り行を除く 1 行 = 1 claim
    * 箇条書きは 1 項目 = 1 claim (インデントされた継続行は同じ項目へ連結)
    * 地の文は空行区切りの段落を文境界で分割 (短い断片は直前へ連結)
    """
    out: list[dict] = []
    context = ""
    para: list[str] = []
    fence: list[str] | None = None
    fence_mark = ""
    last_bullet: int | None = None

    def flush_para() -> None:
        nonlocal para, last_bullet
        if not para:
            return
        body = " ".join(para).strip()
        para = []
        last_bullet = None
        for piece in split_sentences(body):
            out.append({"context": context, "text": piece})

    for line in text.split("\n"):
        if fence is not None:
            if FENCE_RE.match(line) and line.strip().startswith(fence_mark):
                out.append({"context": context, "text": " ".join(fence).strip()})
                fence = None
                continue
            fence.append(line.strip())
            continue
        if FENCE_RE.match(line):
            flush_para()
            fence, fence_mark = [], FENCE_RE.match(line).group(1)
            continue
        if not line.strip():
            flush_para()
            continue
        if HEADING_RE.match(line):
            flush_para()
            context = HEADING_RE.sub("", line).strip()
            continue
        stripped = QUOTE_RE.sub("", line) if QUOTE_RE.match(line) else line
        if not stripped.strip():
            continue
        if stripped.lstrip().startswith("|"):
            flush_para()
            if TABLE_SEP_RE.match(stripped):
                continue
            cells = [c.strip() for c in stripped.strip().strip("|").split("|")]
            body = " / ".join(c for c in cells if c)
            if body:
                out.append({"context": context, "text": body})
            continue
        if BULLET_RE.match(stripped):
            flush_para()
            out.append({"context": context, "text": BULLET_RE.sub("", stripped).strip()})
            last_bullet = len(out) - 1
            continue
        if last_bullet is not None and INDENT_RE.match(stripped):
            out[last_bullet]["text"] = f"{out[last_bullet]['text']} {stripped.strip()}"
            continue
        para.append(stripped.strip())
    flush_para()
    if fence is not None and fence:
        out.append({"context": context, "text": " ".join(fence).strip()})
    return [c for c in out if c["text"]]


def build_claims(paths: list[pathlib.Path]) -> tuple[list[dict], list[dict]]:
    """入力パス列 → (docs, claims)。check 側もこの関数で再現し照合する。"""
    docs: list[dict] = []
    claims: list[dict] = []
    by_raw: dict[str, str] = {}
    by_norm: dict[str, str] = {}
    seen_path: set[str] = set()
    for i, p in enumerate(paths, 1):
        if not p.is_file():
            die(f"input file not found: {p}")
        key = str(p.resolve())
        if key in seen_path:
            die(f"same input file passed twice: {p}")
        seen_path.add(key)
        raw = p.read_bytes()
        text = norm(raw.decode("utf-8"))
        if not text.strip():
            die(f"input file is empty: {p}")
        raw_sha = hashlib.sha256(raw).hexdigest()
        norm_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        for sha, table, label in ((raw_sha, by_raw, "raw"), (norm_sha, by_norm, "正規化後")):
            if sha in table:
                die(f"{p} は {table[sha]} と{label}が同一内容 — 重複入力は件数を歪めるので弾く")
            table[sha] = str(p)
        nid = f"N{i}"
        segs = segment(text)
        if not segs:
            die(f"no claim extracted from {p}")
        for s in segs:
            claims.append(
                {
                    "id": f"C{len(claims) + 1}",
                    "doc": nid,
                    "context": s["context"],
                    "text": s["text"],
                }
            )
        docs.append(
            {
                "id": nid,
                "path": str(p),
                "bytes": len(raw),
                "raw_sha256": raw_sha,
                "norm_sha256": norm_sha,
                "claims": len(segs),
            }
        )
    return docs, claims


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", help="事実・解釈・価値観が混ざった自分のテキスト。並べた順が N1..Nn")
    ap.add_argument("--stance", default="", help="立場ファイル (既定: reference/stance_default.md)")
    ap.add_argument("--work", required=True)
    ap.add_argument("--topic", default="", help="お題 (付録C §7 に出す)")
    ap.add_argument("--overwrite", action="store_true",
                    help="既存の work を上書きで作り直す。本文が同じ claim は裁定を引き継ぐ")
    args = ap.parse_args()

    work = pathlib.Path(args.work)
    work.mkdir(parents=True, exist_ok=True)

    # --- 何も書く前に既存生成物を検査する (裁定済み台帳を黙って壊さない) -------
    existing = [n for n in ("ledger.json", "claims.json", "manifest.json") if (work / n).exists()]
    if existing and not args.overwrite:
        die(
            f"{work/existing[0]} already exists — 既存の run を作り直すなら --overwrite を付ける"
            "（本文が同じ claim の裁定は引き継がれる）。別サフィックスの run は作らない"
        )

    stance_p = pathlib.Path(args.stance) if args.stance else DEFAULT_STANCE
    if not stance_p.is_file():
        die(f"stance file not found: {stance_p}")
    stance_raw = stance_p.read_bytes()
    stance = norm(stance_raw.decode("utf-8"))
    if not stance.strip():
        die(f"stance file is empty: {stance_p} — 立場が空だと V(価値基準) の由来が書けない")
    anchors = stance_anchors(stance)
    if len(anchors) < 2:
        die(f"{stance_p} に `## {ANCHOR_HEADING}` 配下の箇条書きが 2 件未満 — "
            "価値基準の由来と表明が『この立場に固有か』を検査できない。"
            f"reference/stance_default.md の `## {ANCHOR_HEADING}` を見て、"
            "自分の立場を指す語（例: 職種 / 家族構成）を 2 件以上列挙すること")

    paths = [pathlib.Path(n) for n in args.inputs]
    docs, claims = build_claims(paths)

    # --- --overwrite: 旧台帳を読んでから生成物を消す (検査はすべて通過済み) ----
    carried, dropped_basis = 0, []
    # 新 claim の位置 -> 引き継ぐ旧行。同じ (context, text) が複数あるときは
    # 出現順に 1 対 1 で対応させる (先頭の旧行へ寄せない)。
    match: dict[int, dict] = {}
    old_to_new: dict[str, str] = {}
    if args.overwrite:
        # 入力・立場が消す対象の中にあれば、何も消さずに止める
        # シンボリックリンクを解決した形と、リンクのまま (abspath) の形の両方で見る
        # （work/evidence/ に外部原本へのリンクを置いた場合も、リンクごと消えるので止める）
        targets = {f(work / n) for n in GENERATED
                   for f in (lambda q: q.resolve(), lambda q: pathlib.Path(os.path.abspath(q)))}
        for src in [*paths, stance_p]:
            for rp in (src.resolve(), pathlib.Path(os.path.abspath(src))):
                t = next((t for t in targets if rp == t or t in rp.parents), None)
                if t is not None:
                    die(f"--overwrite: {src} は上書きで消える {t} の中にある — "
                        "入力・立場ファイルを work の生成物の外へ移してから再実行すること（何も消していない）")
    if args.overwrite and (work / "ledger.json").exists():
        # 照合キーは (入力ファイルのパス, context, text)。N 番号は入力の並べ替えで変わるので使わず、
        # 旧 claims.json の docs から旧行の入力パスを引く。パスは abspath で比べ、
        # 改名・別 CWD からの相対指定などで一致しない入力の claim は引き継がない (未裁定に戻る)。
        queues: dict[tuple[str, str, str], list[dict]] = {}
        try:
            old_ledger = json.loads((work / "ledger.json").read_text(encoding="utf-8"))
            old_claims = json.loads((work / "claims.json").read_text(encoding="utf-8"))
            # 旧入力の相対パスは旧 init 時の CWD (manifest の cwd) を基準に絶対化する。
            # cwd の記録が無い旧 run では、相対パスの入力は引き継がない (取り違えるよりは未裁定)。
            old_mani = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
            old_cwd = old_mani.get("cwd") if isinstance(old_mani, dict) else None
            old_doc_path = {}
            for d in old_claims.get("docs", []):
                if os.path.isabs(d["path"]):
                    old_doc_path[d["id"]] = os.path.abspath(d["path"])
                elif isinstance(old_cwd, str) and old_cwd:
                    old_doc_path[d["id"]] = os.path.abspath(os.path.join(old_cwd, d["path"]))
            for r in old_ledger.get("rows", []):
                op = old_doc_path.get(r.get("doc", ""))
                if op is None:
                    continue
                queues.setdefault((op, r.get("context", ""), r.get("text", "")), []).append(r)
        except (OSError, ValueError, AttributeError, KeyError, TypeError) as e:
            die(f"--overwrite: 旧 {work/'ledger.json'} / {work/'claims.json'} / {work/'manifest.json'}"
                f" が読めない ({e}) — 何も消していない")
        new_doc_path = {d["id"]: os.path.abspath(d["path"]) for d in docs}
        for i, c in enumerate(claims):
            q = queues.get((new_doc_path[c["doc"]], c["context"], c["text"]))
            if q:
                o = q.pop(0)
                match[i] = o
                if o.get("id"):
                    old_to_new[o["id"]] = c["id"]
    if args.overwrite:
        for name in GENERATED:
            t = work / name
            if t.is_symlink():
                t.unlink()  # リンクだけを外す (リンク先には触れない。rmtree はリンクを拒否する)
            elif t.is_dir():
                shutil.rmtree(t)
            elif t.exists():
                t.unlink()

    claims_obj = {"docs": docs, "claims": claims}
    claims_bytes = json.dumps(claims_obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
    (work / "claims.json").write_text(
        json.dumps(claims_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    rows = []
    for i, c in enumerate(claims):
        row = {"id": c["id"], "doc": c["doc"], "context": c["context"], "text": c["text"],
               **{k: (v.copy() if isinstance(v, (dict, list)) else v)
                  for k, v in LEDGER_FIELDS.items()}}
        o = match.get(i)
        if o:
            ob = o.get("basis") if isinstance(o.get("basis"), list) else []
            lost = [b for b in ob if b not in old_to_new]
            if lost:
                # 根拠の claim が消えた・変わった解釈は、残りの basis だけで成り立つとは
                # 限らないので引き継がず未裁定に戻す
                dropped_basis += [f"{c['id']}:{b}" for b in lost]
            else:
                for k in LEDGER_FIELDS:
                    if k in o:
                        row[k] = o[k]
                row["basis"] = [old_to_new[b] for b in ob]
                carried += 1
        rows.append(row)
    ledger = {"rows": rows}
    (work / "ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (work / "stance.md").write_text(stance + "\n", encoding="utf-8")

    lines = ["## 7. 付録C. 入力と作成条件", ""]
    if args.topic:
        lines.append(f"- お題: {args.topic}")
    lines += [
        f"- 入力テキスト: {len(docs)} 件 / 抽出 claim: {len(claims)} 件",
        f"- 立場ファイル: `{stance_p}`（検査用アンカー: {' / '.join(anchors)}）",
        "",
        "| ID | ファイル | 文字量(byte) | claim 数 | sha256(raw, 先頭16) |",
        "| -- | -------- | ------------ | -------- | ------------------- |",
    ]
    for d in docs:
        lines.append(
            f"| {d['id']} | `{d['path']}` | {d['bytes']} | {d['claims']} | `{d['raw_sha256'][:16]}` |"
        )
    lines += ["", "> 本節はスクリプト生成物 (`work/section0.md`)。件数は手で書かない。"]
    (work / "section0.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    (work / "manifest.json").write_text(
        json.dumps(
            {
                "generated": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "topic": args.topic,
                "inputs": [str(p) for p in paths],
                "cwd": os.getcwd(),  # --overwrite で旧入力の相対パスを解決する基準
                "stance": str(stance_p),
                "stance_raw_sha256": hashlib.sha256(stance_raw).hexdigest(),
                "claims_sha256": hashlib.sha256(claims_bytes).hexdigest(),
                "docs": len(docs),
                "claims": len(claims),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"OK: {len(docs)} inputs -> {len(claims)} claims")
    if args.overwrite:
        print(f"  overwrite: 裁定を引き継いだ claim {carried} 件 / 未裁定 {len(claims) - carried} 件"
              " — 証跡は消したので fetch_sources.py から回し直す")
        if dropped_basis:
            print("  WARN: 根拠の claim が消えた・変わったため未裁定に戻した解釈"
                  f"（新CID:旧basis）: {', '.join(dropped_basis)}")
    print(f"  {work/'claims.json'}\n  {work/'ledger.json'}\n  {work/'section0.md'}")


if __name__ == "__main__":
    main()
