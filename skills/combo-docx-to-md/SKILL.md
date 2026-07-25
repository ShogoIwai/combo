---
name: combo-docx-to-md
description: Convert .docx design specs to markdown with OCR for embedded images
---
# combo-docx-to-md — convert a .docx document to Markdown

One task = one goal. The goal is verified before the task is reported as
complete; if it is not met, the task loops until it is met or the loop limit is
reached.

---

## Task Summary

| Item                     | Content                                                                                                                                    |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **What it does**   | Converts one `.docx` design specification into clean Markdown, preserving heading hierarchy and tables, optionally appending OCR text for embedded images. |
| **Input**          | A readable `.docx` file; optional output path; optional `--ocr`. See **Prerequisites** for the tools each mode needs.                     |
| **type**                 | `one-shot` — the output `.md` is fully regenerated from the input on every run and overwritten in place; existing output is never used as a source. |
| **Output**         | A single `.md` file (default: input basename with `.md`) — see **Output Structure**.                                                      |
| **goal**           | The output `.md` exists and faithfully represents the input document: every heading and table of the `.docx` is present, and no conversion step reported an unhandled error. |
| **Verification**         | Run `convert.sh` and check exit code 0. Then confirm the output file is non-empty, its heading count is >= the heading count of the source docx, every source table appears as a Markdown table, and stderr contains no `Error:`. With `--ocr`, confirm an OCR section exists for each extracted image. |
| **loop limit** | 3                                                                                                                                          |

## When to Use

- Converting vendor design specs to searchable markdown
- Extracting text content from `.docx` files for review or summarization
- Processing documents with embedded diagrams/tables that need OCR

## Prerequisites

1. **python-docx** — always required, for structured extraction
2. **LibreOffice** — only if the docx contains EMF images (EMF → PNG)
3. **Ollama + glm-ocr model** — only for `--ocr` (`ollama pull glm-ocr:bf16`)

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
   Prerequisites for the requested mode are satisfied. If anything is missing, do
   not start: report `blocked` (not `failed`) with what is missing and how to
   resume.
3. **Handle existing output.** This task is `one-shot`. If the output `.md`
   already exists, confirm it is a file this skill may replace, then regenerate
   it in full without reading its contents.
4. **Do the work.**
   - Extract text using python-docx with proper heading hierarchy
   - Extract images from `word/media/` in the docx (ZIP format)
   - Convert EMF → PNG via LibreOffice if needed
   - OCR each image page-by-page using Ollama + GLM-OCR (`--ocr` only)
   - Combine text + OCR results into the final markdown
5. **Verify.** Run the verification in the Task Summary. Never mark PASS by
   assumption — the check must actually be executed. Also confirm the output file
   exists at the stated path and is readable by a downstream task.
6. **Loop on failure.** If the goal is not met, find the cause, fix it and verify
   again. Stop when any of these holds: the goal is met; the loop limit (3) is
   reached; the same cause failed twice in a row; the result did not improve
   since the previous attempt; or the cause cannot be resolved inside this task
   (missing input, missing tool, environment fault).
7. **Report.** State `done` / `failed` / `blocked`, plus the output path,
   verification result, number of attempts, any remaining cause, and what is
   needed to resume.

## Output Structure

- Original document structure (headings, paragraphs)
- Tables in markdown format
- OCR'd image content at the end (`--ocr` only, when images were present)
