#!/usr/bin/env python3
"""init_ledger.py — カンファレンス参加メモを決定的に「主張(claim)単位」へ切り出し、
裏取り台帳の骨格と 付録C(§6 入力一覧) を起こす。

分類(事実/私見)も裏取りも、ここではしない。ここでやるのは
  * 入力の読み込みと正規化
  * claim への一意 ID 付与 (C1..Cn) — 以後の引用キー
  * 台帳骨格 work/ledger.json (全 claim ぶんの未裁定行)
  * work/section0.md (付録C 入力一覧・件数。件数を人が書かないための機械生成物)
だけ。判断は LLM、集計と網羅は決定的スクリプト、という分担を崩さない。

セグメンテーションは check_report.py の G0 で**入力から再現・照合**される。
つまり claims.json / ledger.json の text を後から書き換えて「裏取りしやすい主張」に
すり替えることはできない。

usage:
  python3 init_ledger.py <notes1.md> [<notes2.txt> ...] --stance <stance.md> --work <work>
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

BULLET_RE = re.compile(r"^\s*(?:[-*+]\s+|[・•]\s*|\(?\d{1,2}[.)）]\s*|[①-⑳]\s*)")
HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
QUOTE_RE = re.compile(r"^\s*>+\s?")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
INDENT_RE = re.compile(r"^(\s+|\t)")
SENT_SPLIT_RE = re.compile(r"(?<=[。．!?！？])\s*")
MIN_SENT = 25  # これ未満の断片は直前の claim へ寄せる (粒度が字数で揺れないように)

LEDGER_FIELDS = {
    "kind": "",              # fact | opinion
    "kind_rationale": "",    # なぜその kind か (行ごとに固有の文)
    "status": "",            # fact のみ: CONFIRMED|CORRECTED|PARTIAL|PRIVATE_PRIMARY|UNVERIFIED
    "restated": "",          # 裏取り後の事実としての言い直し (fact のみ / §1 事実編の本文はこれと逐語一致)
    "status_rationale": "",  # なぜその status か (行ごとに固有の文)
    "searched": {},          # UNVERIFIED のみ: {"queries": [...], "domains": [...]}
    "sources": [],           # 公開出典: [{"title","url","publisher","date","accessed","quote"}]
    "private_sources": [],   # 非公開の一次資料 (PRIVATE_PRIMARY のみ):
                             #   [{"document","page","classification","holder","quote"}]
}


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
    """行ベースの決定的セグメンテーション。

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
            die(f"notes file not found: {p}")
        key = str(p.resolve())
        if key in seen_path:
            die(f"same notes file passed twice: {p}")
        seen_path.add(key)
        raw = p.read_bytes()
        text = norm(raw.decode("utf-8"))
        if not text.strip():
            die(f"notes file is empty: {p}")
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
    ap.add_argument("notes", nargs="+", help="参加メモ (txt/md)。並べた順が N1..Nn")
    ap.add_argument("--stance", required=True, help="自分の立場を書いたファイル (txt/md)")
    ap.add_argument("--work", required=True)
    ap.add_argument("--conference", default="", help="カンファレンス名 (付録C §6 に出す)")
    args = ap.parse_args()

    work = pathlib.Path(args.work)
    work.mkdir(parents=True, exist_ok=True)

    # --- 何も書く前に既存生成物を検査する (裁定済み台帳を壊さない) -------------
    for name in ("ledger.json", "claims.json", "manifest.json"):
        if (work / name).exists():
            die(
                f"{work/name} already exists — 上書きすると裁定済みの内容が消える。"
                "作り直すなら work を明示的に消してから再実行すること"
            )

    stance_p = pathlib.Path(args.stance)
    if not stance_p.is_file():
        die(f"stance file not found: {stance_p}")
    stance_raw = stance_p.read_bytes()
    stance = norm(stance_raw.decode("utf-8"))
    if not stance.strip():
        die(f"stance file is empty: {stance_p} — 立場が空だと §2 の考察が誰宛か決まらない")

    paths = [pathlib.Path(n) for n in args.notes]
    docs, claims = build_claims(paths)

    claims_obj = {"docs": docs, "claims": claims}
    claims_bytes = json.dumps(claims_obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
    (work / "claims.json").write_text(
        json.dumps(claims_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    ledger = {
        "rows": [
            {"id": c["id"], "doc": c["doc"], "context": c["context"], "text": c["text"],
             **{k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in LEDGER_FIELDS.items()}}
            for c in claims
        ]
    }
    (work / "ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (work / "stance.md").write_text(stance + "\n", encoding="utf-8")

    lines = ["## 6. 付録C. 入力と作成条件", ""]
    if args.conference:
        lines.append(f"- 対象カンファレンス: {args.conference}")
    lines += [
        f"- 参加メモ: {len(docs)} 件 / 抽出 claim: {len(claims)} 件",
        f"- 立場ファイル: `{stance_p}`",
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
                "conference": args.conference,
                "notes": [str(p) for p in paths],
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

    print(f"OK: {len(docs)} notes -> {len(claims)} claims")
    print(f"  {work/'claims.json'}\n  {work/'ledger.json'}\n  {work/'section0.md'}")


if __name__ == "__main__":
    main()
