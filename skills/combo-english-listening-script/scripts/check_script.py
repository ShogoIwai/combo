#!/usr/bin/env python3
"""Gate checker for combo-english-listening-script outputs.

Usage:
  python3 check_script.py --file <work/<slug>_<yymmdd>.md> --theme "<テーマ>"

Reads the single merged file, which holds three sections:
"## 状況", "## 会話" (Japanese) and "## Conversation" (English).

Exit 0 = all gates PASS, 1 = some gate FAIL, 2 = input error (missing file/section).
"""
import argparse
import re
import sys
from pathlib import Path

SECTIONS = ("状況", "会話", "Conversation")
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


def split_sections(text):
    """Map each "## <name>" heading to the text up to the next "## " heading."""
    out, name, buf = {}, None, []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if name is not None:
                out[name] = "\n".join(buf)
            name, buf = m.group(1), []
        elif name is not None:
            buf.append(line)
    if name is not None:
        out[name] = "\n".join(buf)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--theme", required=True)
    a = ap.parse_args()

    f = Path(a.file)
    if not f.is_file():
        print(f"ERROR: {f} is not a file", file=sys.stderr)
        return 2
    text = f.read_text(encoding="utf-8")
    secs = split_sections(text)
    missing = [s for s in SECTIONS if s not in secs]
    if missing:
        print(f"ERROR: missing section(s) {', '.join('## ' + s for s in missing)} in {f.name}",
              file=sys.stderr)
        return 2

    title = text.split("\n## ", 1)[0]
    sit, ja, en = secs["状況"], secs["会話"], secs["Conversation"]
    ja_p, en_p, sit_p = body_paragraphs(ja), body_paragraphs(en), body_paragraphs(sit)

    results = []

    def gate(name, ok, detail=""):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}")

    # G1 theme is recorded verbatim in the title line.
    gate("G1 theme", a.theme.strip() in title, f"'{a.theme}' in title of {f.name}")

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
