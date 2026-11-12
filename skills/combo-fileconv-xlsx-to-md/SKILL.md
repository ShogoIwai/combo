---
name: combo-fileconv-xlsx-to-md
description: Convert .xlsx/.xlsm/.xls spreadsheets to Markdown tables, optionally OCR-ing embedded images. Use when spreadsheet data must become reviewable, Git-friendly Markdown.
---
# combo-fileconv-xlsx-to-md — convert a spreadsheet to Markdown tables

One task = one goal. The goal is verified before the task is reported as
complete; if it is not met, the task loops until it is met or the loop limit is
reached.

---

## Task Summary

| Item                     | Content                                                                                                                                        |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| **What it does**   | Converts one `.xlsx` / `.xlsm` / `.xls` workbook into a single Markdown file with one `## Sheet Name` section and table per sheet, optionally appending an OCR section per embedded image. |
| **Input**          | A readable `.xlsx` / `.xlsm` / `.xls` file; optional output path; optional `--ocr` or `--list-sheets`; optional `--records FILE` (`.xlsx`/`.xlsm` only). See **Prerequisites** for the library each format needs. |
| **type**                 | `one-shot` — the output `.md` is fully regenerated from the workbook on every run and overwritten in place; existing output is never used as a source. |
| **Output**         | A single `.md` file (default: input basename with `.md`) — see **Output Structure**. `--list-sheets` writes no file; it prints to stdout and exits. |
| **goal**           | The output `.md` exists and is non-empty, and every sheet that holds data appears as a `## Sheet Name` section whose table has a header row, a separator row and a consistent column count. |
| **Verification**         | Run `convert.sh`; exit code must be 0. Enumerate the workbook's sheets **and their emptiness** directly (openpyxl/xlrd — `--list-sheets` cannot do this, see **Limitations**) and require a `## ` section in the output for each sheet with data. For each table, check the separator row is present and every row's pipe count matches the header's. stderr must contain no `Error:` **and no `Warning: could not read sheet`** (an unreadable sheet is skipped, not failed). With `--records`, the `meta` line's `sha256` must match the input and its `stats` (cells, formulas, merged ranges, comments) must equal the counts in the raw workbook XML; report `stats.uncached_formulas` > 0 to the caller, because those cells are empty in the Markdown. `<launch-root>/combo/tests/fileconv/run_regression.py` implements these checks from raw OOXML. With `--ocr`, require one OCR section per extracted `.png`/`.jpg`/`.jpeg` (EMF images included, via their PNG conversion) and no `Warning:` line — a blank OCR result is dropped and reported as a warning, so a missing section always shows up on stderr. |
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
<launch-root>/combo/skills/combo-fileconv-xlsx-to-md/convert.sh input.xlsx [output.md]

# With OCR for embedded images (requires Ollama running)
<launch-root>/combo/skills/combo-fileconv-xlsx-to-md/convert.sh input.xlsx --ocr [output.md]

# Also write source-addressed records (JSONL); the Markdown is unchanged
<launch-root>/combo/skills/combo-fileconv-xlsx-to-md/convert.sh input.xlsx out.md --records out.records.jsonl

# Print the sheet names to stdout and exit (after the wrapper's banner lines)
<launch-root>/combo/skills/combo-fileconv-xlsx-to-md/convert.sh input.xlsx --list-sheets
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

## Layout mode (agent workflow, opt-in)

For sheets whose meaning is in the layout — forms, Excel 方眼紙, several tables
side by side, flowcharts drawn with shapes — the default single table per
sheet is unreadable. Same three stages as the PDF skill:

```bash
T=<launch-root>/combo/skills/combo-fileconv-xlsx-to-md
python3 $T/xlsx_prepare.py book.xlsx work/ [--sheets A,B]   # deterministic
# model: write work/structured.md from work/draft.md, layout.jsonl and the renders
python3 $T/xlsx_check.py work/structured.md work/layout.jsonl  # gate, exit 1 on FAIL
```

- `xlsx_prepare.py` writes `layout.jsonl` (cell text **as Excel displays it**
  via LibreOffice, merges, `region` = each separate block of cells, shapes,
  and `edge` = connector networks resolved to shape → shape, including buses
  that merge and fork), one render per sheet in `sheets/`, and `draft.md`:
  one table per region plus a Mermaid flowchart. The draft passes the gate.
  Stacked copy-pasted shapes are marked `duplicate_of` and not required twice.
- Model rules: start each sheet with `<!-- sheet: NAME -->`; copy text from
  the records, never retype it; restructure freely (split tables, turn a merged
  procedure grid into lists) but keep every value; summaries go in
  `<!-- interpretation -->` regions; anything read only from the render goes in
  `<!-- vision -->` regions, or between `%% vision` / `%% /vision` lines inside
  a Mermaid block (e.g. an arrow the prepare step left `dangling`).
- The gate checks per sheet: anchors, omission and coverage of the displayed
  text, invented tokens, and that every Mermaid edge joins shapes the source
  connects and every source edge is drawn. It cannot see a value moved to the
  wrong row with the same spelling.

## Output Structure

- Sheet headings (`## Sheet Name`) with a Markdown table per sheet
- The first retained row of each sheet becomes the table header, whether or not
  the workbook treats it as one
- Cell values rendered through Python `str()`; formulas appear as their cached
  values
- OCR'd image content at the end (`--ocr` only, when OCR-able images were present)

`--records` keeps what the Markdown flattens away, one JSON object per line:
a `meta` line (source path, `sha256`, extractor revision, openpyxl version,
`stats`), then a `sheet` record per sheet (`state` visible/hidden/veryHidden,
`dimensions`, merged ranges; `id` = `sheet:<name>`), a `cell` record per cell
that has a formula, a comment or a non-empty value (`id` = `cell:<sheet>!A1`,
`value` = cached result, `formula` kept **separately** — array / data-table
formulas add `formula_type` and `formula_attrs` — `number_format` when not
General, `merge` on a merge anchor, `comment`), and `shape` / `image` records
from the sheet drawings with their anchor cell (`id` = `<kind>:<sheet>#<n>`;
images carry the media `target`, or `linked: true` for an external link) —
flowchart boxes and text boxes that the Markdown never shows. Values are never
recalculated.

## Limitations

- **`--ocr` is not available for `.xls`**: image extraction is skipped, and the
  run still succeeds.
- **`--list-sheets` is not a clean list**: the wrapper prints its banner, input,
  output and mode lines to stdout first, and the list covers *all* sheets with no
  regard for whether they hold data.
- **Number formatting is not preserved** in the Markdown: values pass through
  `str()`, so display formats, date formats and shown precision are lost
  (`--records` carries the format code, not a rendered display string).
- **A formula whose value was never cached yields an empty cell** — typical for
  workbooks written by a script and never opened in Excel. `--records` exposes
  the formula and counts them in `stats.uncached_formulas`.
- **Shapes, text boxes and pictures appear only in `--records`** (and in
  layout mode), not in the default Markdown. Objects embedded as OLE/VML (e.g. some EMF diagrams) are not listed.
- **OCR is best-effort and never fails the run**: a missing helper, unreachable
  Ollama, absent model, or per-image error is a `Warning:` and the script exits 0.
  Only `.png`/`.jpg`/`.jpeg` are OCR'd (plus EMF converted to PNG by LibreOffice);
  other media are ignored. An image the model returns nothing for is dropped with
  a `Warning:` rather than appended as an empty section.
