#!/usr/bin/env python3
"""md_pdf_qa.py — check a PDF rendered from Markdown and pick pages to look at.

Usage:
    md_pdf_qa.py <input.md> <output.pdf> [--mermaid] [--qa-dir DIR] [--dpi 50]

Deterministic checks (FAIL exits 1):
  pdf        %PDF- magic and >= 1 page
  headings   every heading's text is in the PDF text
  cjk-font   the source has Japanese/CJK text (prose, code or diagrams) ->
             the PDF embeds a CJK font (emb=yes in pdffonts); otherwise the
             glyphs may be tofu even though the text extracts
  code       the first line of every code block is in the PDF text
  tables     every table header cell is in the PDF text
  images     every local image (inline, reference-style, <angle> or <img>)
             exists, and each raster one is matched one-to-one to an embedded
             image of its pixel size and look (a broken link renders as a
             small icon; two same-size images cannot stand in for each other)
  mermaid    with --mermaid (the PDF was made with convert.sh --mermaid):
             >= 95 % of each diagram's label tokens are in the PDF text and
             no diagram's source (header or arrow lines) is;
             without: WARN, the blocks were printed as code
  visual     the contact sheets were made for every page
Visual QA: every page goes into contact sheets (8 pages each) in --qa-dir
(default: <pdf>.qa/), listed as LOOK lines, with the approximate page of each
diagram and wide table as hints. Text checks cannot see clipped diagrams,
overflowing tables or overlapping glyphs, so the sheets must be opened and
looked at before the conversion is reported done.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from md_blocks import fences as md_fences, images as md_images, is_local   # noqa: E402

CJK = re.compile(r"[぀-ヿ㐀-鿿豈-﫿]")
CJK_FONT = re.compile(r"CJK|Gothic|Mincho|IPA|Takao|JP|Meiryo|Yu|Hiragino|VL|Sazanami|Droid Sans Fallback|Source Han|WenQuanYi|Ume", re.I)
TOKEN = re.compile(r"[A-Za-z0-9_]+|[぀-ヿ㐀-鿿豈-﫿]")


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def squash(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s))


def plain(s):
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("\\|", "|")
    s = re.sub(r"(?<![A-Za-z0-9])_([^_]+)_(?![A-Za-z0-9])", r"\1", s)   # _emphasis_, not snake_case
    return re.sub(r"[*`]", "", s).strip()


def thumb(im):
    """16x16 grey thumbnail; transparent pixels count as white, as on the page."""
    from PIL import Image
    im = im.convert("RGBA")
    bg = Image.new("RGBA", im.size, "white")
    return list(Image.alpha_composite(bg, im).convert("L").resize((16, 16)).tobytes())


def unmatched_images(raster, pdf):
    """Local raster files with no embedded image of the same pixel size and look,
    matched one-to-one (pdfimages extracts each embedded image object)."""
    import tempfile
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdfimages", "-png", pdf, os.path.join(td, "i")], capture_output=True)
        rows = [l.split() for l in sh(["pdfimages", "-list", pdf]).splitlines()[2:]]
        # pdfimages numbers files in list order; keep the "image" rows (not smask)
        emb = []
        for k, r in enumerate(rows):
            path = os.path.join(td, f"i-{k:03d}.png")
            if len(r) > 4 and r[2] == "image" and os.path.exists(path):
                with Image.open(path) as im:
                    emb.append(((im.width, im.height), thumb(im)))
    used, absent = set(), []
    for p in raster:
        with Image.open(p) as im:
            size, t = im.size, thumb(im)
        best = min(((sum(abs(x - y) for x, y in zip(t, et)) / 256, j)
                    for j, (es, et) in enumerate(emb) if es == size and j not in used), default=None)
        if best is None or best[0] > 32:
            absent.append(os.path.basename(p))
        else:
            used.add(best[1])
    return absent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("md")
    ap.add_argument("pdf")
    ap.add_argument("--mermaid", action="store_true")
    ap.add_argument("--qa-dir")
    ap.add_argument("--dpi", type=int, default=50)
    a = ap.parse_args()
    fails, warns, looks = [], [], []

    if open(a.pdf, "rb").read(5) != b"%PDF-":
        print("FAIL pdf: not a PDF")
        sys.exit(1)
    n = int((re.search(r"^Pages:\s+(\d+)", sh(["pdfinfo", a.pdf]), re.M) or [0, 0])[1])
    if n < 1:
        fails.append("pdf: no pages")
    pages = [squash(sh(["pdftotext", "-f", str(i), "-l", str(i), a.pdf, "-"])) for i in range(1, n + 1)]
    whole = "".join(pages)
    whole_alnum = re.sub(r"[^0-9A-Za-z_぀-ヿ㐀-鿿豈-﫿]", "", whole)

    src = open(a.md, encoding="utf-8").read()
    fences = md_fences(src)        # same CommonMark parsing as the renderer's pre-render
    prose, last = [], 0
    for s0, e0, _, _ in fences:
        prose.append(src[last:s0])
        last = e0
    prose = "".join(prose) + src[last:]
    prose = re.sub(r"(?s)<!--.*?-->", "", prose)
    prose = re.sub(r'(?s)<div class="mermaid-figure".*?</div>', "", prose)

    for h in re.findall(r"(?m)^#{1,6}\s+(.*?)\s*#*\s*$", prose):
        t = squash(plain(h))
        if t and t not in whole:
            fails.append(f"headings: '{plain(h)[:60]}' not in the PDF text")
    # local images: a link Chromium could not load is drawn as a tiny broken-image
    # icon, so count the real-size images embedded in the PDF
    base = os.path.dirname(os.path.abspath(a.md))
    local = sorted({os.path.normpath(os.path.join(base, d.split("#")[0].split("?")[0]))
                    for _, _, d, _ in md_images(src) if is_local(d)})
    gone = [p for p in local if not os.path.isfile(p)]
    if gone:
        fails.append(f"images: {len(gone)} linked file(s) missing, e.g. {gone[0]}")
    # raster files only (an SVG becomes vectors, not a pdfimages entry)
    raster = [p for p in local if p not in gone and re.search(r"\.(png|jpe?g|gif|bmp|webp)$", p, re.I)]
    if raster:
        try:
            absent = unmatched_images(raster, a.pdf)
            if absent:
                fails.append(f"images: {len(absent)} local image(s) not found embedded in the PDF "
                             f"(broken link?), e.g. {absent[0]}")
        except ImportError:
            warns.append("images: Pillow missing, embedded images not checked")
    if CJK.search(src):
        # pdffonts: name type encoding emb sub uni object ID (type may contain spaces)
        rows = [l.split() for l in sh(["pdffonts", a.pdf]).splitlines()[2:]]
        cjk = [r for r in rows if len(r) >= 6 and CJK_FONT.search(r[0])]
        if not any(r[-5] == "yes" for r in cjk):
            fails.append("cjk-font: Japanese text in the source but no CJK font embedded (tofu risk)"
                         + (f"; {cjk[0][0]} is referenced but not embedded" if cjk else ""))
    n_mm = 0
    mm_src = []
    for start, _, info, body in fences:
        line = src.count("\n", 0, start) + 1
        if (info.split() or [""])[0].lower() == "mermaid":
            n_mm += 1
            mm_src.append((line, body))
            continue
        first = next((l for l in body.splitlines() if l.strip()), "")
        if first and squash(first) not in whole:
            fails.append(f"code: block at line {line} ('{first.strip()[:40]}') not in the PDF text")
    if a.mermaid:
        # rendered diagrams show labels, never their source: a header line
        # ("flowchart TD", "sequenceDiagram") or an arrow line in the text means
        # the block was printed as code after all
        for line, body in mm_src:
            src_lines = [l.strip() for l in body.splitlines() if l.strip() and not l.strip().startswith("%%")]
            shown = [l for l in src_lines[:1] + [l for l in src_lines if re.search(r"--|==>|->>|-\.", l)]
                     if len(squash(l)) >= 6 and squash(l) in whole]
            if shown:
                fails.append(f"mermaid: block at line {line} printed as source ('{shown[0][:40]}'), not drawn")
    if n_mm and not a.mermaid:
        warns.append(f"mermaid: {n_mm} block(s) printed as code; render them with convert.sh --mermaid")

    # things to look at, in document order: (line, label, tokens that locate it)
    items = []
    for m in re.finditer(r"(?m)^\|(.+)\|\s*\n\|\s*:?-{3,}.*\n((?:\|.*\n?)*)", prose):
        cells = [plain(c) for c in re.split(r"(?<!\\)\|", m.group(1))]
        miss = [c for c in cells if c and squash(c) not in whole]
        line = src.count("\n", 0, m.start()) + 1
        if miss:
            fails.append(f"tables: header cell(s) {miss[:3]} of the table at line {line} not in the PDF text")
        rows = m.group(2).splitlines()
        risky = len(cells) >= 5 or any(len(c) > 40 for r in rows for c in r.split("|"))
        if risky:   # wide or long-celled tables are the ones that overflow
            items.append((line, f"table at line {line}", [squash(c) for c in cells if c]))

    if a.mermaid and n_mm:
        # the labels each diagram must show, from the same pre-render convert.sh uses
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "md_prerender.py"),
                                a.md, os.path.join(td, "x.md"), "--manifest", os.path.join(td, "m.json")],
                               capture_output=True, text=True)
            if r.returncode:
                fails.append("mermaid: " + r.stderr.strip()[-300:])
            manifest = json.load(open(os.path.join(td, "m.json"))) if r.returncode == 0 else []
        for d in manifest:
            toks = TOKEN.findall(unicodedata.normalize("NFKC", d["labels"]))
            if not toks:
                continue
            hit = sum(1 for t in toks if t in whole_alnum)
            if hit / len(toks) < 0.95:
                fails.append(f"mermaid: diagram {d['index']} (line {d['line']}): only {hit}/{len(toks)} label tokens in the PDF text")
            items.append((d["line"], f"diagram {d['index']} (line {d['line']})", toks))

    targets, cursor = {}, 0   # page -> labels; search forward only, as content is in order
    for line, label, keys in sorted(items):
        score = [sum(1 for k in keys if k in pages[i]) for i in range(n)]
        # first page from the cursor showing most of it (common words recur later)
        # (one page back: a table split over a page break repeats its header row)
        start = max(0, cursor - 1)
        hit = next((i for i in range(start, n) if score[i] >= 0.8 * len(keys)), None)
        if hit is None and any(score[start:]):
            hit = max(range(start, n), key=lambda i: score[i])
        if hit is not None:
            cursor = hit
            targets.setdefault(hit + 1, []).append(label)

    # Contact sheets of every page (8 per image): locating content by text is
    # approximate, so the whole document is looked at; the located pages are
    # hints for a closer look (pdftoppm -r 100 -f N -l N).
    qa = a.qa_dir or os.path.splitext(a.pdf)[0] + ".qa"
    os.makedirs(qa, exist_ok=True)
    for f in os.listdir(qa):      # a previous run's sheets must not pass for this one's
        if re.match(r"(p-\d+|contact-\d+)\.png$", f):
            os.remove(os.path.join(qa, f))
    r = subprocess.run(["pdftoppm", "-r", str(a.dpi), "-png", a.pdf, os.path.join(qa, "p")],
                       capture_output=True, text=True)
    shots = sorted((f for f in os.listdir(qa) if re.match(r"p-\d+\.png$", f)),
                   key=lambda f: int(re.search(r"\d+", f).group()))
    if r.returncode or len(shots) != n:
        fails.append(f"visual: pdftoppm rendered {len(shots)} of {n} page(s)"
                     + (f" ({r.stderr.strip()[-200:]})" if r.stderr.strip() else ""))
    try:
        from PIL import Image, ImageDraw
        for k in range(0, len(shots), 8):
            ims = [Image.open(os.path.join(qa, f)) for f in shots[k:k + 8]]
            w, h = max(i.width for i in ims), max(i.height for i in ims)
            sheet = Image.new("RGB", (4 * w + 30, 2 * h + 10), "#888")
            for j, im in enumerate(ims):
                x, y = (j % 4) * (w + 10), (j // 4) * (h + 10)
                sheet.paste(im, (x, y))
                ImageDraw.Draw(sheet).text((x + 4, y + 4), str(k + j + 1), fill="red")
            path = os.path.join(qa, f"contact-{k // 8 + 1:02d}.png")
            sheet.save(path)
            looks.append(f"{path}: pages {k + 1}-{k + len(ims)}")
        for f in shots:
            os.remove(os.path.join(qa, f))
    except ImportError:
        looks += [os.path.join(qa, f) for f in shots]
    for pg in sorted(targets):
        looks.append(f"hint page {pg}: {', '.join(targets[pg][:4])}{' …' if len(targets[pg]) > 4 else ''}")

    for x in fails:
        print("FAIL", x)
    for x in warns:
        print("WARN", x)
    for x in looks:
        print("LOOK", x)
    status = "FAIL" if fails else ("WARN" if warns else "PASS")
    print(f"{status}: {n} page(s); look at the contact sheets before reporting done")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
