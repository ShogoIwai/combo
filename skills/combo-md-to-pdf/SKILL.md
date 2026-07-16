---
name: combo-md-to-pdf
description: Convert Markdown (.md) files to PDF with correct Japanese, code blocks and tables via md-to-pdf (Chromium rendering, no LaTeX). Triggers on "mdをPDF化", "markdown to pdf", "md→pdf", "PDFに変換".
---
# md-to-pdf Skill

Convert `.md` files to PDF. Uses **md-to-pdf** (Puppeteer/Chromium rendering),
so Japanese text, code blocks, tables and emoji come out correctly **without a
LaTeX toolchain**. Pandoc is available on the box but has no PDF engine, so this
Chromium path is the reliable default.

## When to Use

- "mdをPDF化して" / "markdown to pdf" / "このドキュメントをPDFに"
- Producing a shareable PDF from a design note / README / report written in Markdown
- Batch-converting a directory of `.md` files

## Prerequisites (one-time)

```bash
npm install -g md-to-pdf
npx puppeteer browsers install chrome   # downloads Chromium into ~/.cache/puppeteer
```

Both commands are one-time setup (Chromium is cached under
`~/.cache/puppeteer/chrome/...`). The `convert.sh` wrapper prints the exact
install commands if `md-to-pdf` is missing.

## Usage

```bash
# Single file  -> input.pdf (same directory)
<launch-root>/combo/skills/combo-md-to-pdf/convert.sh input.md

# Explicit output name
<launch-root>/combo/skills/combo-md-to-pdf/convert.sh input.md out/report.pdf

# Batch: each file -> same-name .pdf
<launch-root>/combo/skills/combo-md-to-pdf/convert.sh docs/*.md

# Override page/margin options (JSON, passed to md-to-pdf --pdf-options)
PDF_OPTIONS='{"format":"A4","margin":"15mm"}' \
  <launch-root>/combo/skills/combo-md-to-pdf/convert.sh input.md
```

`--pdf-options` is **always** passed to md-to-pdf. When `PDF_OPTIONS` is not
set, `convert.sh` applies a default of `{"format":"A4","margin":"15mm"}`; set the
env var to override it. Call the CLI directly with `md-to-pdf input.md` only if
you deliberately want md-to-pdf's own defaults.

## Notes

- No LaTeX required — rendering is done by headless Chromium via Puppeteer.
- First run after a fresh clone needs the Chromium download (one-time, ~150MB).
- For pandoc-based PDF instead, a PDF engine (weasyprint / tectonic / xelatex)
  must be installed separately — not present by default here.
