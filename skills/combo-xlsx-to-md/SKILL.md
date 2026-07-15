---
name: combo-xlsx-to-md
description: Convert .xlsx/.xls spreadsheets to Markdown tables with optional OCR for embedded images
---
# xlsx-to-md Skill

Convert `.xlsx` / `.xls` spreadsheet files to clean Markdown tables.
Each sheet becomes a section with its own table; empty rows/columns are trimmed.
Handles embedded images via OCR when Ollama + GLM-OCR is available.

## When to Use

- Converting design data sheets, spec tables, or BOMs to searchable markdown
- Extracting tabular content from Excel for review, commit diffs, or documentation
- Processing spreadsheets with embedded diagrams/flowcharts that need OCR
- Sharing spreadsheet data in Git-friendly format

## Prerequisites

1. **openpyxl** — for structured extraction (works on `.xlsx` files)
2. **Ollama + glm-ocr model** — optional, for image OCR (`ollama pull glm-ocr:bf16`)

## Usage

```bash
# Basic conversion (all sheets → single .md file)
<launch-root>/combo/skills/combo-xlsx-to-md/convert.sh input.xlsx [output.md]

# With OCR for embedded images (requires Ollama running)
<launch-root>/combo/skills/combo-xlsx-to-md/convert.sh input.xlsx --ocr [output.md]

# Only sheet names in the output
<launch-root>/combo/skills/combo-xlsx-to-md/convert.sh input.xlsx --list-sheets
```

## Workflow

1. **Open workbook** with openpyxl, enumerate all sheets
2. **For each sheet**: read rows, trim trailing empty cells/rows
3. **Format as Markdown table** per sheet, separated by headings
4. **Extract embedded images** from `xl/media/` in the xlsx (ZIP format) — if `--ocr`
5. **OCR each image** page-by-page using Ollama + GLM-OCR
6. **Combine** tables + OCR results into final markdown

## Output Structure

The output `.md` file contains:

- Sheet headings (`## Sheet Name`) with Markdown tables per sheet
- Column headers from the first row of each sheet
- OCR'd image content at the end (if `--ocr` was used and images were present)

## Notes

- If openpyxl is not installed, the script will print a hint and exit 1
- Empty sheets are skipped with a warning
- Numbers are preserved as-is; formulas are exported as their cached values
- For `.xls` (binary) files, install `xlrd` — the script falls back automatically
- OCR requires Ollama running with `glm-ocr:bf16` model pulled
- Re-running overwrites the output `.md` in place
