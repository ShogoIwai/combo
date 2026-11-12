---
name: combo-fileconv-docx-to-md
description: Convert .docx design specs to markdown, optionally OCR-ing embedded images. Use when a .docx must become searchable/reviewable Markdown.
---
# combo-fileconv-docx-to-md — convert a .docx document to Markdown

One task = one goal. The goal is verified before the task is reported as
complete; if it is not met, the task loops until it is met or the loop limit is
reached.

---

## Task Summary

| Item                     | Content                                                                                                                                    |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **What it does**   | Converts one `.docx` into Markdown by walking the body **in document order** (paragraphs and tables interleaved, body-level content controls included): headings from `Heading N` styles or an outline level on the paragraph/style chain, TOC entries as an indented list, tables as Markdown tables, text boxes as `>` quotes after their anchor, then optionally an OCR section per embedded image. Text is the **accepted view** of tracked changes. Optionally writes source-addressed records (`--records`). |
| **Input**          | A readable `.docx` file; optional output path; optional `--ocr`; optional `--records FILE`. See **Prerequisites** for the tools each mode needs. |
| **type**                 | `one-shot` — the output `.md` is fully regenerated from the input on every run and overwritten in place; existing output is never used as a source. |
| **Output**         | A single `.md` file (default: input basename with `.md`) — see **Output Structure**.                                                      |
| **goal**           | The output `.md` exists and is non-empty; every `Heading N` paragraph and every top-level table of the source appears, **in source order**; no TOC entry is rendered as a heading; every non-empty body paragraph's text is present; every table row has the header's column count; and — when `--ocr` was requested — an OCR section is present for every image the script actually OCRs (see **Limitations**). With `--records`, the records file starts with a `meta` line whose `sha256` matches the input. |
| **Verification**         | Run `convert.sh`; exit code must be 0 (a python-docx exception is now printed on stderr). Then check the goal against the source read **independently of the converter** — `<launch-root>/combo/tests/fileconv/run_regression.py` implements these checks from raw OOXML (`table-count`, `table-shape`, `order`, `block-order`, `toc-not-heading`, `paragraph-coverage`, `records-meta`); for a document outside its corpus, run the same checks by hand or add it to the local `corpus.local.tsv`. With `--ocr`, stderr must contain **no `Warning:` line** (OCR degrades via warnings, not failures) and the output must hold one OCR section per `word/media/*.png`/`.jpg`/`.jpeg` plus per EMF converted to PNG — a blank OCR result is dropped and reported as a warning, so a missing section always shows up on stderr. |
| **loop limit** | 3                                                                                                                                          |

## When to Use

- Converting vendor design specs to searchable markdown
- Extracting text content from `.docx` files for review or summarization
- Processing documents with embedded diagrams/tables that need OCR

## Prerequisites

1. **python3 + python-docx >= 1.0** — always required, for structured extraction
2. **unzip** — only for `--ocr` (image extraction from the docx ZIP)
3. **LibreOffice** — only for `--ocr`, and only when the docx holds EMF images;
   they are converted to PNG before OCR. A PNG-only docx converts without it,
   and text-only conversion never invokes it at all.
4. **curl, jq, and an executable OCR helper** — only for `--ocr`. The helper
   defaults to `ocr_to_md.sh` in the shared `ollama/` directory next to `combo/`;
   set `OCR_TO_MD=/path/to/ocr_to_md.sh` if it lives elsewhere.
5. **Ollama + glm-ocr model** — only for `--ocr` (`ollama pull glm-ocr:bf16`)

## Usage

```bash
# Basic conversion (text only)
<launch-root>/combo/skills/combo-fileconv-docx-to-md/convert.sh input.docx [output.md]

# With OCR for embedded images (requires Ollama running)
<launch-root>/combo/skills/combo-fileconv-docx-to-md/convert.sh input.docx --ocr [output.md]

# Also write source-addressed records (JSONL) for consistency checks
<launch-root>/combo/skills/combo-fileconv-docx-to-md/convert.sh input.docx out.md --records out.records.jsonl
```

`--records` does not change the Markdown. The file holds a `meta` line (source
path, `sha256`, extractor revision, python-docx version, `view:
accepted-changes`) and one line per emitted element with a stable address:
`b<N>` body paragraph (`kind` heading / toc / body, with `level` and `style`;
an explicit outline level 9 "body text" on the paragraph or a style stops a
heading inherited from a base style),
`t<K>.r<I>.c<J>` table cell, `b<N>.x<M>` text box.

## Procedure

1. **Fix the goal.** Read the Task Summary goal, input, output and verification
   and treat them as the only completion criteria for this run. Do not relax the
   goal mid-run; if it must change, stop and report why.
2. **Check the inputs.** Confirm the `.docx` exists and is readable and that the
   Prerequisites for the requested mode are satisfied. The script itself does not
   preflight the OCR toolchain — it degrades to text-only — so check it here. If
   anything is missing, do not start: report `blocked` (not `failed`) with what is
   missing and how to resume.
3. **Handle existing output.** This task is `one-shot`. If the output `.md`
   already exists, confirm it is a file this skill may replace, then regenerate
   it in full without reading its contents.
4. **Do the work.**
   - `docx_to_md.py` walks the body in document order: headings from `Heading N`
     or an outline level (capped at `######`), TOC entries as a list, tables in
     place, text boxes after their anchor; `--records` adds the records file
   - `--ocr` only: extract `word/media/` from the docx (ZIP format), convert EMF →
     PNG via LibreOffice, OCR each PNG/JPEG through Ollama + GLM-OCR, and
     append the results
5. **Verify.** Run the verification in the Task Summary. Never mark PASS by
   assumption — the check must actually be executed. Exit code 0 alone is not
   sufficient for `--ocr`: a skipped or failed OCR still exits 0. Also confirm the
   output file exists at the stated path and is readable by a downstream task.
6. **Loop on failure.** If the goal is not met, find the cause, fix it and verify
   again. Stop when any of these holds: the goal is met; the loop limit (3) is
   reached; the same cause failed twice in a row; the result did not improve
   since the previous attempt; or the cause cannot be resolved inside this task
   (missing input, missing tool, environment fault).
7. **Report.** State `done` / `failed` / `blocked`, plus the output path,
   verification result, number of attempts, any remaining cause, and what is
   needed to resume.

## Output Structure

- Paragraphs and tables in document order; headings from `Heading N` styles or
  an outline level; TOC entries as an indented `-` list (never headings)
- Table cells escaped (`\|`, `\\`), in-cell line breaks as `<br>`; a text box
  inside a cell is folded into that cell
- Text boxes anchored in a body paragraph as `>` quote lines right after it
- OCR'd image content at the end (`--ocr` only, when OCR-able images were present)

## Limitations

- **Tracked changes are shown accepted**: inserted and moved-to text is kept;
  deleted and moved-from content (including deleted breaks) and field
  instructions are dropped; comments, footnotes and headers/footers
  are not emitted.
- **Headings need a style signal**: a `Heading N` style or an outline level.
  A heading made only of direct formatting (bold, size) stays a paragraph.
- **Merged table cells repeat their text** in every grid column they span
  (python-docx semantics); nested tables are flattened into their cell.
- **Images are not placed in the text**: OCR sections (`--ocr`) are appended at
  the end in media-file order, not at the figure position.
- **OCR is best-effort and never fails the run.** A missing OCR helper,
  unreachable Ollama, absent model, or a per-image OCR error is reported as
  `Warning:` and the script still exits 0 with text-only output.
- **The OCR section is omitted entirely** when the document has no OCR-able image
  (a plain note on stdout) or when no image yielded text (a `Warning:` on stderr);
  the run still succeeds either way.
- **OCR covers PNG and JPEG** (plus EMF converted to PNG). Embedded GIF/TIFF are
  extracted but skipped.
