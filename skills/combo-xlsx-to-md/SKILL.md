---
name: combo-xlsx-to-md
description: Convert .xlsx/.xlsm/.xls spreadsheets to Markdown tables, optionally OCR-ing embedded images. Use when spreadsheet data must become reviewable, Git-friendly Markdown.
---
# combo-xlsx-to-md — convert a spreadsheet to Markdown tables

One task = one goal. The goal is verified before the task is reported as
complete; if it is not met, the task loops until it is met or the loop limit is
reached.

---

## Task Summary

| Item                     | Content                                                                                                                                        |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| **What it does**   | Converts one `.xlsx` / `.xlsm` / `.xls` workbook into a single Markdown file with one `## Sheet Name` section and table per sheet, optionally appending an OCR section per embedded image. |
| **Input**          | A readable `.xlsx` / `.xlsm` / `.xls` file; optional output path; optional `--ocr` or `--list-sheets`. See **Prerequisites** for the library each format needs. |
| **type**                 | `one-shot` — the output `.md` is fully regenerated from the workbook on every run and overwritten in place; existing output is never used as a source. |
| **Output**         | A single `.md` file (default: input basename with `.md`) — see **Output Structure**. `--list-sheets` writes no file; it prints to stdout and exits. |
| **goal**           | The output `.md` exists and is non-empty, and every sheet that holds data appears as a `## Sheet Name` section whose table has a header row, a separator row and a consistent column count. |
| **Verification**         | Run `convert.sh`; exit code must be 0. Enumerate the workbook's sheets **and their emptiness** directly (openpyxl/xlrd — `--list-sheets` cannot do this, see **Limitations**) and require a `## ` section in the output for each sheet with data. For each table, check the separator row is present and every row's pipe count matches the header's. stderr must contain no `Error:` **and no `Warning: could not read sheet`** (an unreadable sheet is skipped, not failed). With `--ocr`, require one OCR section per extracted `.png`/`.jpg`/`.jpeg` (EMF images included, via their PNG conversion) and no `Warning:` line — a blank OCR result is dropped and reported as a warning, so a missing section always shows up on stderr. |
| **loop limit** | 3                                                                                                                                              |

## When to Use

- Converting design data sheets, spec tables, or BOMs to searchable markdown
- Extracting tabular content from Excel for review, commit diffs, or documentation
- Processing spreadsheets with embedded diagrams/flowcharts that need OCR
- Sharing spreadsheet data in Git-friendly format

## Prerequisites

1. **python3** — always required
2. **openpyxl** — required for `.xlsx` / `.xlsm`. If it is missing the script
   prints `Error: openpyxl is required for .xlsx files.` and exits 1 (no install
   hint is printed).
3. **xlrd** — required for legacy `.xls`
4. **unzip, curl, jq, and an executable OCR helper** — only for `--ocr`. The
   helper defaults to `ocr_to_md.sh` in the shared `ollama/` directory next to
   `combo/`; set `OCR_TO_MD=/path/to/ocr_to_md.sh` if it lives elsewhere.
5. **LibreOffice** — only for `--ocr`, and only when the workbook holds EMF
   images (Visio/PowerPoint pastes); they are converted to PNG before OCR
6. **Ollama + glm-ocr model** — only for `--ocr` (`ollama pull glm-ocr:bf16`)

## Usage

```bash
# Basic conversion (all sheets → single .md file)
<launch-root>/combo/skills/combo-xlsx-to-md/convert.sh input.xlsx [output.md]

# With OCR for embedded images (requires Ollama running)
<launch-root>/combo/skills/combo-xlsx-to-md/convert.sh input.xlsx --ocr [output.md]

# Print the sheet names to stdout and exit (after the wrapper's banner lines)
<launch-root>/combo/skills/combo-xlsx-to-md/convert.sh input.xlsx --list-sheets
```

## Procedure

1. **Fix the goal.** Read the Task Summary goal, input, output and verification
   and treat them as the only completion criteria for this run. Do not relax the
   goal mid-run; if it must change, stop and report why.
2. **Check the inputs.** Confirm the workbook exists and is readable and that the
   Prerequisites for the requested format and mode are satisfied. If anything is
   missing, do not start: report `blocked` (not `failed`) with what is missing and
   how to resume.
3. **Handle existing output.** This task is `one-shot`. If the output `.md`
   already exists, confirm it is a file this skill may replace, then regenerate
   it in full without reading its contents.
4. **Do the work.**
   - Open the workbook (openpyxl for `.xlsx`/`.xlsm`, xlrd for `.xls`) and
     enumerate its sheets
   - For each sheet: read rows, drop trailing empty cells and empty rows, skip
     sheets with no data
   - Emit a `## Sheet Name` heading and a Markdown table per sheet
   - `--ocr` only: extract `xl/media/` from the workbook (ZIP format), OCR each
     `.png`/`.jpg`/`.jpeg` through Ollama + GLM-OCR, and append the results
5. **Verify.** Run the verification in the Task Summary. Never mark PASS by
   assumption — the check must actually be executed. Exit code 0 alone is not
   sufficient: unreadable sheets and failed OCR are warnings, not failures. Also
   confirm the output file exists at the stated path and is readable by a
   downstream task.
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
- The first retained row of each sheet becomes the table header, whether or not
  the workbook treats it as one
- Cell values rendered through Python `str()`; formulas appear as their cached
  values
- OCR'd image content at the end (`--ocr` only, when OCR-able images were present)

## Limitations

- **`--ocr` is not available for `.xls`**: image extraction is skipped, and the
  run still succeeds.
- **`--list-sheets` is not a clean list**: the wrapper prints its banner, input,
  output and mode lines to stdout first, and the list covers *all* sheets with no
  regard for whether they hold data.
- **Number formatting is not preserved**: values pass through `str()`, so display
  formats, date formats and shown precision are lost. A formula whose value was
  never cached yields an empty cell.
- **OCR is best-effort and never fails the run**: a missing helper, unreachable
  Ollama, absent model, or per-image error is a `Warning:` and the script exits 0.
  Only `.png`/`.jpg`/`.jpeg` are OCR'd (plus EMF converted to PNG by LibreOffice);
  other media are ignored. An image the model returns nothing for is dropped with
  a `Warning:` rather than appended as an empty section.
