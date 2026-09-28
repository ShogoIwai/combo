#!/usr/bin/env python3
"""Gate checker for combo-english-listening-script outputs.

Usage:
  python3 check_script.py --dir <work/<slug>_<yymmdd>> --theme "<テーマ>"

Exit 0 = all gates PASS, 1 = some gate FAIL, 2 = input error (missing dir/file).
"""
import argparse
import re
import sys
from pathlib import Path

FILES = ("ja_situation.md", "ja_conversation.md", "en_conversation.md")
JA_MIN, JA_MAX = 900, 1100

JP_CHARS = re.compile(r"[぀-ヿ㐀-鿿！-～]")
# Speaker labels: "A:", "店員：", "Tom:", "【客】" etc. at the start of an utterance.
JA_LABEL = re.compile(r"^\s*(?:【[^】]{1,12}】|[^\s：:。、「」]{1,12}[：:])")
EN_LABEL = re.compile(r"^\s*(?:\[[^\]]{1,20}\]|[A-Z][A-Za-z .'-]{0,20}:\s)")
# Stage directions / supplements in brackets.
JA_STAGE = re.compile(r"[（(][^）)]*[）)]")
EN_STAGE = re.compile(r"\([^)]*\)|\[[^\]]*\]|\*[^*]+\*")


def body_paragraphs(text):
    """Utterances = blank-line separated paragraphs, excluding markdown headings."""
    paras = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l for l in block.splitlines() if l.strip() and not l.lstrip().startswith("#")]
        if lines:
            paras.append("\n".join(lines))
    return paras


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--theme", required=True)
    a = ap.parse_args()

    d = Path(a.dir)
    if not d.is_dir():
        print(f"ERROR: {d} is not a directory", file=sys.stderr)
        return 2
    missing = [f for f in FILES if not (d / f).is_file()]
    if missing:
        print(f"ERROR: missing {', '.join(missing)} in {d}", file=sys.stderr)
        return 2

    sit = (d / "ja_situation.md").read_text(encoding="utf-8")
    ja = (d / "ja_conversation.md").read_text(encoding="utf-8")
    en = (d / "en_conversation.md").read_text(encoding="utf-8")
    ja_p, en_p, sit_p = body_paragraphs(ja), body_paragraphs(en), body_paragraphs(sit)

    results = []

    def gate(name, ok, detail=""):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}")

    # G1 theme is recorded verbatim in the situation file.
    gate("G1 theme", a.theme.strip() in sit, f"'{a.theme}' in ja_situation.md")

    # G2 situation is non-empty Japanese prose.
    gate("G2 situation", bool(sit_p) and bool(JP_CHARS.search("".join(sit_p))),
         f"{len(sit_p)} paragraph(s)")

    # G3 Japanese conversation length (whitespace excluded).
    n = len(re.sub(r"\s", "", "".join(ja_p)))
    gate("G3 ja length", JA_MIN <= n <= JA_MAX, f"{n} chars (target {JA_MIN}-{JA_MAX})")

    # G4 conversation has at least a few turns.
    gate("G4 ja turns", len(ja_p) >= 6, f"{len(ja_p)} utterances (>=6)")

    # G5 no speaker labels / stage directions in the Japanese conversation.
    bad = [p.splitlines()[0][:30] for p in ja_p if JA_LABEL.search(p) or JA_STAGE.search(p)]
    gate("G5 ja no labels", not bad, "; ".join(bad[:3]))

    # G6 no speaker labels / stage directions in the English conversation.
    bad = [p.splitlines()[0][:30] for p in en_p if EN_LABEL.search(p) or EN_STAGE.search(p)]
    gate("G6 en no labels", not bad, "; ".join(bad[:3]))

    # G7 English conversation contains no Japanese characters.
    bad = [p.splitlines()[0][:30] for p in en_p if JP_CHARS.search(p)]
    gate("G7 en is English", bool(en_p) and not bad, "; ".join(bad[:3]))

    # G8 utterance-by-utterance alignment between ja and en.
    gate("G8 ja/en aligned", len(ja_p) == len(en_p), f"ja={len(ja_p)} en={len(en_p)}")

    ok = all(results)
    print(f"{'PASS' if ok else 'FAIL'} {sum(results)}/{len(results)} gates")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
