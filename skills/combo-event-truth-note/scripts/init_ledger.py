#!/usr/bin/env python3
"""イベントメモを決定的に単位分割し、裁定前の台帳と作業ファイルを起こす。

  python3 init_ledger.py <memo.md> [...] --work <work> [--event "<名前>"]

出力:
  work/units.json     分割結果（不変）
  work/ledger.json    {"rows": [...]} 裁定 + ファクトチェック用の空欄つき台帳
  work/digs.json      深掘り調査（D1..）。初期は空
  work/essence.json   エッセンス（E1..）。初期は空
  work/manifest.json  入力の sha256 と units_sha256

work に既存生成物があるときは何も書かずに終了する（作り直すなら work を消す）。
rows の id/doc/context/text は以後 **書き換えてはいけない**（G0 が再分割して照合する）。
"""
import argparse
import hashlib
import json
import os
import re
import sys

SENT_END = re.compile(r"(?<=[。！？!?])\s*")
# 箇条書きマーカー（半角・全角・丸数字）。引用記号 `>` は落とさない（発言の引用と
# 自分の記述の区別が消えるため、本文の一部として残す）
#   半角形（`- ` `1. `）は空白を必須にする（`-5度` `1.5倍` を壊さないため）。
#   全角形（`１．` `①`）は空白なしでも箇条書きなので、空白を任意にする。
BULLET = re.compile(r"^\s*(?:(?:[-*・]|\d+[.)]|[（(]\d+[）)])[ \t　]+"
                    r"|(?:[０-９]+[.．)）]|[①-⑳])[ \t　]*)")
# 見出しは `#` のあとに空白を要求する（`#hashtag` を見出しとして食わない）
HEADING = re.compile(r"^\s*(#{1,6})[ \t　]+(.+?)\s*$")
FENCE = re.compile(r"^\s*(```+|~~~+)")

OUTPUTS = ("units.json", "ledger.json", "digs.json", "essence.json", "manifest.json")


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def segment(text: str):
    """行 → 見出し文脈つきの単位列。改行と句点だけで切る決定的な処理。

    - 見出しは階層つきの文脈（`親 > 子`）として持ち、単位にはしない。
    - コードフェンスの中は **1 行 1 単位のまま**扱う（句点で切らない・箇条書き
      マーカーを剥がさない）。ログや設定を貼っただけの行を壊さないため。
    - 1 文字の応答（「嫌」「え」）も落とさない。感情はしばしばそこに出る。
    """
    units = []
    stack = []          # (level, title)
    in_fence = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if not line.strip():
            continue
        ctx = " > ".join(t for _, t in stack)
        if in_fence:
            units.append({"context": ctx, "text": line.strip()})
            continue
        m = HEADING.match(line)
        if m:
            lv = len(m.group(1))
            while stack and stack[-1][0] >= lv:
                stack.pop()
            stack.append((lv, m.group(2)))
            continue
        body = BULLET.sub("", line).strip()
        if not body:
            continue
        for part in SENT_END.split(body):
            part = part.strip()
            if part:
                units.append({"context": ctx, "text": part})
    return units


def blank_row(u):
    return {
        **u,
        # --- 層の裁定 ---
        "layer": "",              # 事実 / 感情 / 思考 / 欲求 / 借り物
        "layer_rationale": "",    # その行固有の理由（30 字以上）
        "own_words": "",          # 自分の言葉での言い直し
        "body_sense": "",         # からだの感覚（任意）
        "unknown": False,
        "unknown_note": "",
        "borrowed_phrase": "",    # layer=借り物 のとき、なぞってしまった原文
        "charge": 0,              # 0-5 引っかかりの強さ
        # --- ファクトチェック（layer=事実 のみ）---
        "status": "",             # CONFIRMED / CORRECTED / PARTIAL / UNVERIFIED / PRIVATE_PRIMARY
        "status_rationale": "",
        "corrected_to": "",       # CORRECTED のとき、公開情報側の正しい内容
        "sources": [],            # {title,url,publisher,date,accessed,quote}
        "private_sources": [],    # {document,page,classification,holder,quote}
        "searched": {},           # UNVERIFIED のとき {"queries": [...], "domains": [...]}
    }


def build(paths, work, event):
    units, docs = [], []
    for i, p in enumerate(paths, 1):
        did = f"N{i}"
        with open(p, encoding="utf-8") as f:
            text = f.read()
        docs.append({"id": did, "path": os.path.abspath(p), "sha256": sha256_text(text)})
        for u in segment(text):
            units.append({"id": f"O{len(units) + 1}", "doc": did, **u})
    if not units:
        sys.exit("[ERROR] メモから単位が 1 件も取れなかった。入力を確認すること。")

    manifest = {"event": event, "docs": docs,
                "units_sha256": sha256_text(json.dumps(units, ensure_ascii=False, sort_keys=True))}

    os.makedirs(work, exist_ok=True)
    payload = {
        "units.json": units,
        "ledger.json": {"rows": [blank_row(u) for u in units]},
        "digs.json": [],
        "essence.json": [],
        "manifest.json": manifest,
    }
    for name, obj in payload.items():
        with open(os.path.join(work, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    print(f"[OK] {len(units)} units -> {work}/ledger.json （digs/essence は空。手順 5〜7 で埋める）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("memos", nargs="+")
    ap.add_argument("--work", required=True)
    ap.add_argument("--event", default="")
    a = ap.parse_args()
    for name in OUTPUTS:
        if os.path.exists(os.path.join(a.work, name)):
            sys.exit(f"[ERROR] {a.work}/{name} が既にある。作り直すなら work を消すこと。")
    build(a.memos, a.work, a.event)


if __name__ == "__main__":
    main()
