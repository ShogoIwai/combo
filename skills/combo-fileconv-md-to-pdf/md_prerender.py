#!/usr/bin/env python3
"""md_prerender.py — replace ```mermaid blocks with inline SVG (mermaid-cli).

Usage:
    md_prerender.py <input.md> <output.md> [--manifest out.json]

All blocks are rendered in one mmdc run. Each SVG gets its own id (mmdc
names every diagram `my-svg`, so their style rules would collide) and is
inlined as one HTML line, so diagram labels stay real text in the PDF and can
be checked there. A block that fails to render is an error naming the block
and its line, never a silent fallback to code.

--manifest writes [{index, line, id, labels}] — `labels` is the diagram's
visible text, which md_pdf_qa.py looks for in the PDF.
"""
import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from md_blocks import mermaid_blocks   # noqa: E402  (CommonMark fences, shared with the QA)
TIMEOUT = int(os.environ.get("MERMAID_TIMEOUT", "300"))


def mmdc(src_md, out_md):
    try:
        p = subprocess.run(["mmdc", "-i", src_md, "-o", out_md, "-e", "svg", "-q"],
                           capture_output=True, text=True, timeout=TIMEOUT)
        return p.returncode, p.stderr
    except subprocess.TimeoutExpired:
        return 124, f"mmdc did not finish within {TIMEOUT}s"


def svg_labels(svg):
    body = re.sub(r"(?s)<style.*?</style>", " ", svg)
    body = re.sub(r"<br\s*/?>", " ", body)
    text = html.unescape(re.sub(r"<[^>]+>", " ", body))
    return re.sub(r"\s+", " ", text).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--manifest")
    a = ap.parse_args()
    if not shutil.which("mmdc"):
        sys.exit("Error: mmdc not found (npm install -g @mermaid-js/mermaid-cli)")

    md = open(a.input, encoding="utf-8").read()
    blocks = mermaid_blocks(md)   # [(start, end, info, body)]
    manifest = []
    if blocks:
        work = tempfile.mkdtemp(prefix="mmd-")
        try:
            only = os.path.join(work, "only.md")
            with open(only, "w", encoding="utf-8") as f:
                f.write("".join("```mermaid\n" + b[3] + "\n```\n\n" for b in blocks))
            rc, err = mmdc(only, os.path.join(work, "out.md"))
            svgs = [os.path.join(work, f"out-{i}.svg") for i in range(1, len(blocks) + 1)]
            if rc or not all(os.path.exists(s) for s in svgs):
                # find the culprit(s): render each block alone
                bad = []
                for i, b in enumerate(blocks, 1):
                    one = os.path.join(work, f"one-{i}.md")
                    open(one, "w", encoding="utf-8").write("```mermaid\n" + b[3] + "\n```\n")
                    r1, e1 = mmdc(one, os.path.join(work, f"one-{i}-out.md"))
                    if r1:
                        line = md.count("\n", 0, b[0]) + 1
                        msg = next((l for l in e1.splitlines() if "Error" in l), e1.strip()[-200:])
                        bad.append(f"mermaid block {i} (line {line}): {msg.strip()}")
                sys.exit("Error: " + ("; ".join(bad) if bad else err.strip()[-400:] or "mmdc failed"))
            out, last = [], 0
            for i, (b, path) in enumerate(zip(blocks, svgs), 1):
                svg = open(path, encoding="utf-8").read()
                svg = re.sub(r"^<\?xml[^>]*>\s*", "", svg)
                sid = f"mmd-{i}"
                svg = svg.replace("my-svg", sid)
                svg = re.sub(r"\s*\n\s*", " ", svg)          # one line: a blank line would end the HTML block
                out.append(md[last:b[0]])
                out.append(f'<div class="mermaid-figure" style="page-break-inside: avoid; margin: 1em 0;">{svg}</div>\n')
                last = b[1]
                manifest.append({"index": i, "line": md.count("\n", 0, b[0]) + 1, "id": sid,
                                 "labels": svg_labels(svg)})
            out.append(md[last:])
            md = "".join(out)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    with open(a.output, "w", encoding="utf-8") as f:
        f.write(md)
    if a.manifest:
        with open(a.manifest, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"rendered {len(manifest)} mermaid block(s)", file=sys.stderr)


if __name__ == "__main__":
    main()
