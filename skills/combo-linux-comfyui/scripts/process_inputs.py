#!/usr/bin/env python3
"""prompts/ のベースファイルを、テンプレートで項目ごとに差し替えて inputs/ のプロンプトファイルにする。

  ./process_inputs.py                 # prompts/ の <数字>.txt を全部
  ./process_inputs.py 1 sakura        # prompts/1.txt と prompts/sakura.txt だけ（名前は何でもよい）
  ./process_inputs.py --dry-run       # 書かずに、作る・消すファイルだけ表示

ファイル:
  prompts/<base>.txt                  ベース。「ラベル: 値」の行で項目を書く（例 Person: / Attire: / Place: / Action:）。
                                      ラベル行に続く空行でない行は同じ項目の続き。空行・# 行・次のラベル行で項目が終わる
  prompts/template/template-<N>.txt   差し替え N 番。ベースと同じラベルの項目を書いたものだけ置き換える
                                      （書かなかった項目はベースのまま）。「Negative prompt:」行から末尾はネガティブ。
                                      共通のネガティブは template-1 に書く
  inputs/<base>-<N>.txt               出力（N = 1 〜 テンプレート番号の最大）

動作:
  - ベースの「Negative prompt:」以降は捨て、ネガティブを末尾に付ける。ネガティブは template-<N> に
    「Negative prompt:」があればそれ、無ければ template-1 のもの（2 以降は template-1 の共通ネガティブを使う）。
    template-1 にも無ければネガティブ無し（gen.py はプリセットの negative を使う）。
  - template-1 に項目を書かなければ <base>-1 はベースそのもの（＋ template-1 のネガティブ）。
  - 番号の抜けた template-<N> は空のテンプレート扱い（ベースそのもの＋ template-1 のネガティブ）で WARN を出す。
  - inputs/<base>-<N>.txt は毎回作り直す。テンプレートが減って範囲外になった inputs/<base>-<M>.txt は消す。
    それ以外の inputs/ のファイルには触れない。
  - テンプレートにあってベースに無いラベルは使わず WARN を出す。
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROMPTS = ROOT / "prompts"
TEMPLATES = PROMPTS / "template"
INPUTS = ROOT / "inputs"

# gen.py と同じ判定（前後の空白を無視・大文字小文字を区別しない）
NEG_RE = re.compile(r"^\s*negative prompt:", re.IGNORECASE)
# ラベルは文字（日本語可）で始まり、文字・数字・空白・_・- だけからなる。全角コロンも可
LABEL_RE = re.compile(r"^([^\W\d_][\w \-]*?)\s*[:：]")


def read_lines(path: Path) -> list[str]:
    # BOM は gen.py と同じく捨てる
    lines = path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"      # 末尾に改行が無いと次の行（ネガティブ）とつながってしまう
    return lines


def split_negative(lines: list[str]) -> tuple[list[str], list[str]]:
    """「Negative prompt:」行の前と、その行から末尾まで。"""
    for i, line in enumerate(lines):
        if NEG_RE.match(line):
            return lines[:i], lines[i:]
    return lines, []


def parse_blocks(lines: list[str]) -> list[tuple[str | None, list[str]]]:
    """行を (ラベル, 行のリスト) の並びに分ける。ラベル外の行は (None, [行])。"""
    out: list[tuple[str | None, list[str]]] = []
    cur: list[str] | None = None
    for line in lines:
        m = LABEL_RE.match(line)
        if m:
            cur = [line]
            out.append((m.group(1).strip(), cur))
        elif cur is not None and line.strip() and not line.lstrip().startswith("#"):
            cur.append(line)
        else:
            cur = None
            out.append((None, [line]))
    return out


def build(base_body: list[str], tpl_path: Path | None, common_neg: list[str], out_name: str) -> list[str]:
    tpl_body, tpl_neg = split_negative(read_lines(tpl_path)) if tpl_path else ([], [])
    tpl_neg = tpl_neg or common_neg
    repl: dict[str, list[str]] = {}
    for label, blk in parse_blocks(tpl_body):
        if label is not None:
            repl[label] = blk
    result: list[str] = []
    used: set[str] = set()
    for label, blk in parse_blocks(base_body):
        if label is not None and label in repl:
            result.extend(repl[label])
            used.add(label)
        else:
            result.extend(blk)
    for label in repl.keys() - used:
        print(f"WARN  {out_name}: {tpl_path.name} の {label}: はベースに無いので使いません")
    return result + tpl_neg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bases", nargs="*", help="prompts/ のベース名（.txt 省略可）。省略時は <数字>.txt 全部")
    ap.add_argument("--dry-run", action="store_true", help="書かずに、作る・消すファイルだけ表示")
    args = ap.parse_args()

    templates: dict[int, Path] = {}
    for p in TEMPLATES.glob("template-*.txt"):
        m = re.fullmatch(r"template-(\d+)\.txt", p.name)
        if m:
            templates[int(m.group(1))] = p
    if not templates:
        print(f"error: {TEMPLATES}/template-<N>.txt がありません", file=sys.stderr)
        return 1
    max_idx = max(templates)
    print(f"テンプレート: 1 〜 {max_idx}")
    common_neg = split_negative(read_lines(templates[1]))[1] if 1 in templates else []
    if not common_neg:
        print("WARN  template-1.txt に Negative prompt: が無いので、ネガティブを書いていない番号はネガティブ無し")

    if args.bases:
        bases = []
        for b in args.bases:
            p = PROMPTS / (b if b.endswith(".txt") else f"{b}.txt")
            if not p.is_file():
                print(f"error: ベースがありません: {p}", file=sys.stderr)
                return 1
            bases.append(p)
    else:
        bases = sorted(p for p in PROMPTS.glob("*.txt") if p.stem.isdigit())
        if not bases:
            print(f"error: {PROMPTS}/<数字>.txt がありません（名前で指定するなら引数に）", file=sys.stderr)
            return 1

    if not args.dry_run:
        INPUTS.mkdir(parents=True, exist_ok=True)
    for base in bases:
        stem = base.stem
        base_body, _ = split_negative(read_lines(base))
        # 範囲外になった古い出力だけ消す（<stem>-<数字>.txt に完全一致するものだけ）
        for old in INPUTS.glob("*.txt"):     # ベース名に [ ] 等があっても漏れないよう、絞り込みは正規表現で
            m = re.fullmatch(re.escape(stem) + r"-(\d+)\.txt", old.name)
            if m and int(m.group(1)) > max_idx:
                print(f"DEL   {old.name}")
                if not args.dry_run:
                    old.unlink()
        for idx in range(1, max_idx + 1):
            out_name = f"{stem}-{idx}.txt"
            tpl = templates.get(idx)
            if tpl is None:
                print(f"WARN  {out_name}: template-{idx}.txt が無いのでベースそのまま＋template-1 のネガティブ")
            lines = build(base_body, tpl, common_neg, out_name)
            if not args.dry_run:
                tmp = INPUTS / f".{out_name}.tmp"
                tmp.write_text("".join(lines), encoding="utf-8")
                tmp.replace(INPUTS / out_name)
            if tpl is not None:
                print(f"OK    {out_name}（{base.name} ← {tpl.name}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
