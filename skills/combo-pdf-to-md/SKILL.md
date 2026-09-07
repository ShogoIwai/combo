---
name: combo-pdf-to-md
description: Convert a .pdf (datasheet, spec, scanned document) to page-by-page Markdown, using the text layer and optionally OCR-ing pages that have none. Use when a PDF must become searchable/reviewable Markdown.
---
# combo-pdf-to-md — convert a .pdf document to Markdown

One task = one goal. The goal is verified before the task is reported as
complete; if it is not met, the task loops until it is met or the loop limit is
reached.

---

## Task Summary

| Item | Content |
| ---- | ------- |
| **What it does** | Converts one `.pdf` into a single Markdown file: a title block, then one `## Page N` section per page. Text-layer pages are emitted inside a ` ```text ` fence (fixed-layout, so tables and columns survive; only the form feed and trailing blanks are stripped); OCR'd pages are emitted as the OCR model's Markdown. |
| **Input** | A readable `.pdf` (encrypted only with `--password`); optional output path; optional `--ocr` / `--ocr-fallback` (mutually exclusive); optional `--pages N-M`; optional `--password PW` for an encrypted PDF. See **Prerequisites**. |
| **type** | `one-shot` — the output `.md` is fully regenerated from the input on every run and overwritten in place; existing output is never used as a source. |
| **Output** | A single `.md` file (default: input basename with `.md`) — see **Output Structure**. |
| **goal** | The output `.md` exists and is non-empty; it carries exactly one `## Page N` heading per page of the requested range; and no page in the range is silently missing (a page with neither text layer nor OCR is explicitly marked `_(no extractable text on this page)_`). |
| **Verification** | Run `convert.sh`; exit code must be 0. Then: the output is non-empty; `grep -c '^## Page ' output.md` equals the number of pages in the requested range (a PDF whose own text holds a line like `## Page 3` inflates this count; confirm the page numbers run 1-by-1 when the counts disagree) (`pdfinfo` `Pages:` when `--pages` is not given); and stderr contains **no `Warning:` line** when an OCR mode was requested (OCR degrades via warnings, not failures). For `--ocr`, also confirm the reported `OCR'd pages` count equals the number of pages in the range. For `--ocr-fallback`, zero OCR'd pages is the correct result when every page has a text layer — do not treat it as a failure. |
| **loop limit** | 3 |

## When to Use

- Turning vendor datasheets / design specs into searchable, greppable Markdown
- Feeding a PDF to a downstream text-only task (review, extraction, summarization)
- Getting text out of a **scanned** PDF that has no text layer (`--ocr`)

## Prerequisites

1. **poppler-utils** — always required (`pdftotext`, `pdfinfo`; plus `pdftoppm`
   for the OCR modes)
2. **curl, jq, and an executable OCR helper** — only for the OCR modes. The
   helper defaults to `ocr_to_md.sh` in the shared `ollama/` directory next to
   `combo/`; set `OCR_TO_MD=/path/to/ocr_to_md.sh` if it lives elsewhere.
3. **Ollama + glm-ocr model** — only for OCR modes (`ollama pull glm-ocr:bf16`)

## Usage

```bash
S=<launch-root>/combo/skills/combo-pdf-to-md/convert.sh

# Text layer only (fast; the default)
$S input.pdf [output.md]

# OCR only the pages that have no text layer (mixed scanned/native PDFs)
$S input.pdf --ocr-fallback [output.md]

# OCR every page, ignoring the text layer (fully scanned PDFs)
$S input.pdf --ocr [output.md]

# Restrict to a page range (works with every mode)
$S input.pdf --pages 12-30 [output.md]

# Password-protected PDF (the password is passed to every poppler call)
$S input.pdf --password PW [output.md]
```

Env overrides are those of `ollama/ocr_to_md.sh`: `OCR_MODEL` (default
`glm-ocr:bf16`), `OCR_DPI` (default `200`; raise to `300` for dense figures),
`OLLAMA_HOST`.

## Procedure

1. **Fix the goal.** Read the Task Summary goal, input, output and verification
   and treat them as the only completion criteria for this run. Do not relax the
   goal mid-run; if it must change, stop and report why.
2. **Check the inputs.** Confirm the `.pdf` exists, is readable and is not
   encrypted — or, if it is, that `--password` is supplied (`pdfinfo` must
   return a `Pages:` line) — and that the Prerequisites
   for the requested mode are satisfied. The script degrades OCR to text-only via
   warnings, so check the OCR toolchain here. If anything is missing, do not
   start: report `blocked` (not `failed`) with what is missing and how to resume.
3. **Pick the mode.** Probe the text layer first —
   `pdftotext -l 5 input.pdf - | tr -d '[:space:]' | wc -c`. Near zero means a
   scanned PDF: use `--ocr`. A large PDF that is only partly scanned: use
   `--ocr-fallback`. Otherwise stay on the default text mode — OCR is orders of
   magnitude slower and less faithful for a PDF that already has text.
4. **Handle existing output.** This task is `one-shot`. If the output `.md`
   already exists, confirm it is a file this skill may replace, then regenerate
   it in full without reading its contents.
5. **Do the work.** Run `convert.sh` with the chosen mode. For a long OCR run,
   consider converting in page ranges (`--pages`) to separate files so a failure
   does not discard completed work.
6. **Verify.** Run the verification in the Task Summary. Never mark PASS by
   assumption — the check must actually be executed. Exit code 0 alone is not
   sufficient for an OCR mode: a skipped or per-page-failed OCR still exits 0.
   Also confirm the output file exists at the stated path and is readable by a
   downstream task.
7. **Loop on failure.** If the goal is not met, find the cause, fix it and verify
   again. Stop when any of these holds: the goal is met; the loop limit (3) is
   reached; the same cause failed twice in a row; the result did not improve
   since the previous attempt; or the cause cannot be resolved inside this task
   (missing input, missing tool, environment fault).
8. **Report.** State `done` / `failed` / `blocked`, plus the output path, mode
   used, OCR'd page count, verification result, number of attempts, any remaining
   cause, and what is needed to resume.

## Output Structure

~~~markdown
# <PDF Title metadata, else input basename>

> Source: `input.pdf` — pages 1-42 of 42

## Page 1

```text
<pdftotext -layout output; form feed and trailing blanks stripped>
```

## Page 2

<OCR model Markdown, when this page was OCR'd>
~~~

## Limitations

- **No document structure is recovered.** Headings, lists and tables of a
  text-layer PDF are *not* converted to Markdown constructs — the page text is
  preserved as-is in a fenced block, because PDF carries no heading styles.
  Structure recovery, if needed, is a separate LLM pass over this output.
- **Page-oriented, not flow-oriented.** Paragraphs, tables and sentences split
  across a page break stay split, with running headers/footers left in place.
- **Multi-column pages** are handled only as well as `pdftotext -layout` handles
  them; heavily columned layouts may interleave.
- **OCR is best-effort and never fails the run.** A missing OCR helper,
  unreachable Ollama, absent model, or a per-page OCR error is reported as
  `Warning:` and the script still exits 0 (with a text-only, or partly empty,
  page). This is why the verification requires a clean stderr.
- **OCR is slow** — roughly one vision-model inference per page at `OCR_DPI`.
- **Encrypted PDFs are rejected** (`Encrypted: yes` from `pdfinfo`) rather than
  silently emptied, unless `--password` is given; otherwise decrypt first, e.g.
  `qpdf --decrypt`. The password is passed on the command line, so it appears in
  the process argument list (`ps`), in shell history, and in terminal or agent
  execution logs — do not use `--password` on a shared machine; decrypt the file
  out of band instead.
- **The output may not be the input path**: a run whose output would overwrite
  the source PDF is refused.
- **Trailing whitespace is not preserved** inside the fence, and the fence is
  widened past any backtick run in the page so page text cannot close it early.
- **Images and figures are not extracted** as files; in an OCR mode their content
  appears only as whatever the OCR model transcribes from the page raster.
