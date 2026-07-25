---
name: combo-docx-to-md
description: Convert .docx design specs to markdown, optionally OCR-ing embedded images. Use when a .docx must become searchable/reviewable Markdown.
---
# combo-docx-to-md — convert a .docx document to Markdown

One task = one goal. The goal is verified before the task is reported as
complete; if it is not met, the task loops until it is met or the loop limit is
reached.

---

## Task Summary

| Item                     | Content                                                                                                                                    |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **What it does**   | Converts one `.docx` into Markdown: paragraphs first with heading levels derived from `Heading N` / `TOC N` styles, then every table appended as a Markdown table, then optionally an OCR section per embedded image. |
| **Input**          | A readable `.docx` file; optional output path; optional `--ocr`. See **Prerequisites** for the tools each mode needs.                     |
| **type**                 | `one-shot` — the output `.md` is fully regenerated from the input on every run and overwritten in place; existing output is never used as a source. |
| **Output**         | A single `.md` file (default: input basename with `.md`) — see **Output Structure**.                                                      |
| **goal**           | The output `.md` exists and is non-empty; it carries one Markdown heading per `Heading N`/`TOC N` paragraph and one Markdown table per table of the source; and — when `--ocr` was requested — an OCR section is present for every image the script actually OCRs (see **Limitations**). |
| **Verification**         | Run `convert.sh`; exit code must be 0. Then: the output is non-empty; the count of `^#{1,6} ` lines is >= the number of source paragraphs whose style matches `Heading N`/`TOC N` (read the source with python-docx); the number of Markdown tables equals `len(Document(src).tables)`. With `--ocr`, stderr must contain **no `Warning:` line** (OCR degrades via warnings, not failures) and the output must hold one OCR section per `word/media/*.png` plus per EMF converted to PNG. |
| **loop limit** | 3                                                                                                                                          |

## When to Use

- Converting vendor design specs to searchable markdown
- Extracting text content from `.docx` files for review or summarization
- Processing documents with embedded diagrams/tables that need OCR

## Prerequisites

1. **python3 + python-docx** — always required, for structured extraction
2. **unzip** — only for `--ocr` (image extraction from the docx ZIP)
3. **LibreOffice** — only for `--ocr`, to convert EMF images to PNG.
   Text-only conversion never invokes it, even for an EMF-bearing docx.
4. **curl, jq, and an executable OCR helper** — only for `--ocr`. The helper
   defaults to `ocr_to_md.sh` in the shared `ollama/` directory next to `combo/`;
   set `OCR_TO_MD=/path/to/ocr_to_md.sh` if it lives elsewhere.
5. **Ollama + glm-ocr model** — only for `--ocr` (`ollama pull glm-ocr:bf16`)

## Usage

```bash
# Basic conversion (text only)
<launch-root>/combo/skills/combo-docx-to-md/convert.sh input.docx [output.md]

# With OCR for embedded images (requires Ollama running)
<launch-root>/combo/skills/combo-docx-to-md/convert.sh input.docx --ocr [output.md]
```

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
   - Extract paragraphs with python-docx, mapping `Heading N`/`TOC N` to `#`..`######`
   - Append every table of the document as a Markdown table
   - `--ocr` only: extract `word/media/` from the docx (ZIP format), convert EMF →
     PNG via LibreOffice, OCR each resulting PNG through Ollama + GLM-OCR, and
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

- Paragraphs in document order, with headings from `Heading N` / `TOC N` styles
- All tables, in Markdown, appended **after** the paragraphs
- OCR'd image content at the end (`--ocr` only, when OCR-able images were present)

## Limitations

- **Paragraph/table order is not preserved.** All paragraphs are emitted first,
  then all tables — the original interleaving is lost.
- **Heading detection is style-name based**: only English `Heading N` / `TOC N`,
  capped at level 6. Other or localized heading styles become plain paragraphs.
- **OCR is best-effort and never fails the run.** A missing OCR helper,
  unreachable Ollama, absent model, or a per-image OCR error is reported as
  `Warning:` and the script still exits 0 with text-only output.
- **OCR covers PNG only** (plus EMF converted to PNG). Embedded JPEG/GIF/TIFF are
  extracted but skipped.
- **Table cells are not escaped**: cell text containing `|` or newlines can break
  the generated Markdown table.
- stderr from the extraction step is discarded, so a non-zero exit is detectable
  but the underlying exception is not visible in the log.
