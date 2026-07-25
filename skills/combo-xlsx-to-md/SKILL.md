---
name: combo-xlsx-to-md
description: Convert .xlsx/.xls spreadsheets to Markdown tables with optional OCR for embedded images
---
# combo-xlsx-to-md — convert a spreadsheet to Markdown tables

One task = one goal. The goal is verified before the task is reported as
complete; if it is not met, the task loops until it is met or the loop limit is
reached.

---

## Task Summary

| Item                     | Content                                                                                                                                        |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| **What it does**   | Converts one `.xlsx` / `.xls` workbook into a single Markdown file with one table section per sheet (empty rows/columns trimmed), optionally appending OCR text for embedded images. |
| **Input**          | A readable `.xlsx` / `.xls` file; optional output path; optional `--ocr`. See **Prerequisites** for the tools each mode needs.              |
| **type**                 | `one-shot` — the output `.md` is fully regenerated from the workbook on every run and overwritten in place; existing output is never used as a source. |
| **Output**         | A single `.md` file (default: input basename with `.md`) — see **Output Structure**.                                                        |
| **goal**           | The output `.md` exists and represents the workbook: every non-empty sheet has a section with a well-formed Markdown table, sheets skipped as empty are reported, and no conversion step reported an unhandled error. |
| **Verification**         | Run `convert.sh` and check exit code 0. Compare the section headings in the output against `convert.sh input.xlsx --list-sheets` — every non-empty sheet must be present. Confirm each table has a header row and separator row with matching column counts, and that stderr contains no `Error:`. With `--ocr`, confirm an OCR section exists for each extracted image. |
| **loop limit** | 3                                                                                                                                              |

## When to Use

- Converting design data sheets, spec tables, or BOMs to searchable markdown
- Extracting tabular content from Excel for review, commit diffs, or documentation
- Processing spreadsheets with embedded diagrams/flowcharts that need OCR
- Sharing spreadsheet data in Git-friendly format

## Prerequisites

1. **openpyxl** — always required, for structured extraction of `.xlsx`.
   If it is missing, the script prints a hint and exits 1.
2. **xlrd** — only for binary `.xls` files; the script falls back automatically
3. **Ollama + glm-ocr model** — only for `--ocr` (`ollama pull glm-ocr:bf16`)

## Usage

```bash
# Basic conversion (all sheets → single .md file)
<launch-root>/combo/skills/combo-xlsx-to-md/convert.sh input.xlsx [output.md]

# With OCR for embedded images (requires Ollama running)
<launch-root>/combo/skills/combo-xlsx-to-md/convert.sh input.xlsx --ocr [output.md]

# Only sheet names in the output
<launch-root>/combo/skills/combo-xlsx-to-md/convert.sh input.xlsx --list-sheets
```

## Procedure

1. **Fix the goal.** Read the Task Summary goal, input, output and verification
   and treat them as the only completion criteria for this run. Do not relax the
   goal mid-run; if it must change, stop and report why.
2. **Check the inputs.** Confirm the workbook exists and is readable and that the
   Prerequisites for the requested mode are satisfied. If anything is missing, do
   not start: report `blocked` (not `failed`) with what is missing and how to
   resume.
3. **Handle existing output.** This task is `one-shot`. If the output `.md`
   already exists, confirm it is a file this skill may replace, then regenerate
   it in full without reading its contents.
4. **Do the work.**
   - Open the workbook with openpyxl and enumerate all sheets
   - For each sheet: read rows, trim trailing empty cells/rows; skip empty sheets
     with a warning
   - Format as a Markdown table per sheet, separated by headings
   - Extract embedded images from `xl/media/` in the xlsx (ZIP format) — `--ocr` only
   - OCR each image page-by-page using Ollama + GLM-OCR — `--ocr` only
   - Combine tables + OCR results into the final markdown
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

- Sheet headings (`## Sheet Name`) with a Markdown table per sheet
- Column headers taken from the first row of each sheet
- Numbers preserved as-is; formulas exported as their cached values
- OCR'd image content at the end (`--ocr` only, when images were present)
