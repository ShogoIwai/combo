---
name: combo-md-to-pdf
description: Convert Markdown (.md) files to PDF with correct Japanese, code blocks and tables via md-to-pdf (Chromium rendering, no LaTeX). Triggers on "mdをPDF化", "markdown to pdf", "md→pdf", "PDFに変換".
---
# combo-md-to-pdf — convert Markdown to PDF

One task = one goal. The goal is verified before the task is reported as
complete; if it is not met, the task loops until it is met or the loop limit is
reached.

---

## Task Summary

| Item                     | Content                                                                                                                                    |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **What it does**   | Renders one or more `.md` files to PDF with **md-to-pdf** (Puppeteer/headless Chromium), so Japanese text, code blocks, tables and emoji come out correctly without a LaTeX toolchain. |
| **Input**          | One or more readable `.md` files; optional explicit output path; optional `PDF_OPTIONS` JSON. See **Prerequisites** for the tools needed.  |
| **type**                 | `one-shot` — each PDF is fully re-rendered from its Markdown source on every run and overwritten in place; an existing PDF is never used as a source. |
| **Output**         | One `.pdf` per input file — the explicit output path if given, otherwise the input basename with `.pdf` in the same directory.            |
| **goal**           | Every requested PDF exists, is a valid non-empty PDF with at least one page, and renders the constructs its source actually contains — Japanese text without tofu, and code blocks and tables intact. |
| **Verification**         | Run `convert.sh`; exit code must be 0. For each output: the file exists, is non-empty, starts with the `%PDF-` magic bytes, and `pdfinfo` reports >= 1 page. Then, **only for the constructs present in that source**, extract text with `pdftotext` and confirm the Japanese runs, the code-block text and the table cell text all survive; inspect the rendered page visually when layout or glyph coverage is in doubt, since text extraction cannot detect tofu or broken table borders. Confirm one PDF was produced per input file. |
| **loop limit** | 3                                                                                                                                          |

## When to Use

- "mdをPDF化して" / "markdown to pdf" / "このドキュメントをPDFに"
- Producing a shareable PDF from a design note / README / report written in Markdown
- Batch-converting a directory of `.md` files

## Prerequisites

```bash
npm install -g md-to-pdf
npx puppeteer browsers install chrome   # one-time Chromium download
```

1. **md-to-pdf + a Puppeteer Chromium** — always required; the browser download
   is a one-time setup step. `convert.sh` prints the exact install commands when
   `md-to-pdf` is missing, but it does **not** preflight Chromium — that failure
   surfaces as an md-to-pdf error.
2. **A CJK font installed system-wide** — required for Japanese output. Chromium
   alone does not guarantee glyph coverage; without a suitable font the text
   renders as tofu while the command still succeeds.
3. **poppler-utils (`pdfinfo`, `pdftotext`)** — required by the Verification step.

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

## Procedure

1. **Fix the goal.** Read the Task Summary goal, input, output and verification
   and treat them as the only completion criteria for this run. Do not relax the
   goal mid-run; if it must change, stop and report why.
2. **Check the inputs.** Confirm every `.md` input exists and is readable and
   that the Prerequisites are satisfied — the wrapper only checks that
   `md-to-pdf` is on `PATH` and that it received arguments, and leaves input
   errors to md-to-pdf. Note which of Japanese text / code blocks / tables each
   source contains, since that determines what step 5 must check. If anything is
   missing, do not start: report `blocked` (not `failed`) with what is missing and
   how to resume.
3. **Handle existing output.** This task is `one-shot`. If a target `.pdf`
   already exists, confirm it is a file this skill may replace, then re-render it
   in full from the Markdown source.
4. **Do the work.** Run `convert.sh` over the inputs, passing `PDF_OPTIONS` when a
   non-default page format or margin is required. For a batch, keep the
   input → output mapping so each result can be checked individually.
5. **Verify.** Run the verification in the Task Summary. Never mark PASS by
   assumption — a non-zero exit or a 0-byte PDF is a FAIL, and a PDF that renders
   Japanese as tofu is a FAIL even if the command succeeded.
6. **Loop on failure.** If the goal is not met, find the cause, fix it and verify
   again. Stop when any of these holds: the goal is met; the loop limit (3) is
   reached; the same cause failed twice in a row; the result did not improve
   since the previous attempt; or the cause cannot be resolved inside this task
   (missing Chromium, missing font, unreadable input).
7. **Report.** State `done` / `failed` / `blocked`, plus the output paths,
   verification result, number of attempts, any remaining cause, and what is
   needed to resume.

## Limitations

- **Batch vs. explicit-output is decided by a filename heuristic**: exactly two
  arguments whose second ends in `.pdf` always mean input + explicit output. A
  two-file batch whose second input is named `*.pdf` cannot be expressed.
- Exit codes: missing `md-to-pdf` exits 1, no arguments exits 2; conversion,
  `mkdir` and `mv` errors propagate under `set -e`.
