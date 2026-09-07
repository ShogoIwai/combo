#!/bin/bash
# convert.sh — Convert .xlsx / .xls spreadsheets to Markdown tables.
#
# Usage:
#   ./convert.sh <input.xlsx> [output.md] [--list-sheets] [--ocr]
#
# Options:
#   --list-sheets    List sheet names and exit (no conversion)
#   --ocr            Enable OCR for embedded images (requires Ollama + glm-ocr)
#
# Requires:
#   - openpyxl (pip install openpyxl) — for .xlsx files
#   - xlrd  (pip install xlrd)        — optional, for legacy .xls binary files
#   - Ollama with glm-ocr model       — only if --ocr is used

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# OCR helper: the shared ollama/ocr_to_md.sh (sibling of combo/ in the workspace).
# Override with OCR_TO_MD if it lives elsewhere.
OCR_TO_MD="${OCR_TO_MD:-$SCRIPT_DIR/../../../ollama/ocr_to_md.sh}"

INPUT=""
OUTPUT=""
LIST_SHEETS=0
ENABLE_OCR=0

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --list-sheets) LIST_SHEETS=1; shift ;;
        --ocr)         ENABLE_OCR=1; shift ;;
        --help|-h)
            echo "Usage: $0 <input.xlsx> [output.md] [--list-sheets] [--ocr]"
            exit 0
            ;;
        *)
            if [[ -z "$INPUT" ]]; then
                INPUT="$1"
            elif [[ -z "$OUTPUT" ]]; then
                OUTPUT="$1"
            fi
            shift
            ;;
    esac
done

if [[ -z "$INPUT" ]]; then
    echo "Error: input file required" >&2
    exit 1
fi

if [[ ! -f "$INPUT" ]]; then
    echo "Error: input file not found: $INPUT" >&2
    exit 1
fi

# Default output: same dir, change extension to .md
OUTPUT="${OUTPUT:-${INPUT%.*}.md}"

echo "=== xlsx-to-md Converter ==="
echo "Input:  $INPUT"
echo "Output: $OUTPUT"
if [[ $LIST_SHEETS -eq 1 ]]; then
    echo "Mode:   list sheets only"
fi
if [[ $ENABLE_OCR -eq 1 ]]; then
    echo "OCR:    enabled (for embedded images)"
else
    echo "OCR:    disabled (text only)"
fi
echo ""

# Step 1: Extract text using openpyxl/xlrd
echo "[Step 1/3] Converting sheets to Markdown..."

python3 -c "
import sys
import os

try:
    import openpyxl as _xl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

try:
    import xlrd as _xr
    HAS_XLRD = True
except ImportError:
    HAS_XLRD = False

input_path  = sys.argv[1]
output_path = sys.argv[2] if len(sys.argv) > 2 else ''
list_only   = sys.argv[3] == '1' if len(sys.argv) > 3 else False

ext = os.path.splitext(input_path)[1].lower()

if ext in ('.xlsx', '.xlsm'):
    if not HAS_OPENPYXL:
        print('Error: openpyxl is required for .xlsx files.', file=sys.stderr)
        sys.exit(1)
elif ext == '.xls':
    if not HAS_XLRD:
        print('Error: xlrd is required for legacy .xls files.', file=sys.stderr)
        sys.exit(1)
else:
    if not HAS_OPENPYXL and not HAS_XLRD:
        print('Error: neither openpyxl nor xlrd is installed.', file=sys.stderr)
        sys.exit(1)

# USE_OPENPYXL tracks which library actually opened this workbook. It must not
# be confused with HAS_OPENPYXL (merely: openpyxl is importable). A legacy .xls
# is always read with xlrd, even on a machine that also has openpyxl installed.
if ext in ('.xlsx', '.xlsm') and HAS_OPENPYXL:
    wb = _xl.load_workbook(input_path, read_only=True, data_only=True)
    USE_OPENPYXL = True
elif HAS_XLRD:
    wb = _xr.open_workbook(input_path)
    USE_OPENPYXL = False
elif HAS_OPENPYXL:
    wb = _xl.load_workbook(input_path, read_only=True, data_only=True)
    USE_OPENPYXL = True
else:
    print('Error: unsupported format.', file=sys.stderr)
    sys.exit(1)

def is_blank(v):
    # openpyxl yields None for an empty cell; str(None) is 'None', so None must
    # be tested before falling back to the string form.
    return v is None or str(v).strip() == ''

def trim_cells(rows):
    trimmed = []
    for row in rows:
        r = list(row)
        while r and is_blank(r[-1]):
            r.pop()
        if r:
            trimmed.append(r)
    while trimmed and all(is_blank(c) for c in trimmed[-1]):
        trimmed.pop()
    return trimmed

def rows_to_md(rows):
    if not rows:
        return ''
    ncols = max(len(r) for r in rows)
    lines = []
    for i, row in enumerate(rows):
        # Replace newlines inside cells so each table row stays on one line.
        # Markdown renderers that support HTML (<br>) will display them;
        # plain-text viewers see the literal <br>, which is still readable.
        NL = chr(10)   # newline
        BS = chr(92)   # backslash
        cells = []
        for c in row:
            s = str(c) if c is not None else ''
            s = s.replace(BS, BS+BS)       # backslash → double backslash (for | escape)
            s = s.replace('|', BS+'|')     # pipe chars
            s = s.replace(NL, '<br>')      # newlines → <br>
            cells.append(s.strip())
        while len(cells) < ncols:
            cells.append('')
        lines.append('| ' + ' | '.join(cells) + ' |')
        if i == 0:
            lines.append('| ' + ' | '.join(['---'] * ncols) + ' |')
    return '\n'.join(lines) + '\n\n'

sheet_names = list(wb.sheetnames) if USE_OPENPYXL else list(wb.sheet_names())

if list_only:
    for name in sheet_names:
        print(name)
    sys.exit(0)

md_lines = [f'# {os.path.basename(input_path)}\n\n']

for sname in sheet_names:
    try:
        if USE_OPENPYXL:
            ws = wb[sname]
            rows_raw = list(ws.iter_rows(values_only=True))
        else:
            ws = wb.sheet_by_name(sname)
            rows_raw = [[ws.cell_value(r, c) for c in range(ws.ncols)] for r in range(ws.nrows)]
    except Exception as e:
        print(f'Warning: could not read sheet \"{sname}\": {e}', file=sys.stderr)
        continue

    rows = trim_cells(rows_raw)
    if not rows:
        print(f'Skipping empty sheet: {sname}', file=sys.stderr)
        continue

    md_lines.append(f'## {sname}\n\n')
    md_lines.append(rows_to_md(rows))
    md_lines.append('---\n\n')

output_text = ''.join(md_lines).rstrip() + '\n'

if output_path:
    if os.path.dirname(output_path):
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(output_text)
else:
    sys.stdout.write(output_text)
" "$INPUT" "${OUTPUT%.md}_temp.md" "$LIST_SHEETS"

# --list-sheets prints names to stdout and writes no temp file; stop here.
[[ $LIST_SHEETS -eq 1 ]] && exit 0

LINE_COUNT=$(wc -l < "${OUTPUT%.md}_temp.md")
echo "  → Sheet conversion complete ($LINE_COUNT lines)"

# Step 2: Extract embedded images (if OCR enabled, only for .xlsx)
if [[ $ENABLE_OCR -eq 1 ]]; then
    EXT="${INPUT##*.}"
    if [[ "$EXT" == "xls" ]]; then
        echo ""
        echo "[Step 2/3] Skipping image extraction: legacy .xls format has no embedded images." >&2
    else
        echo ""
        echo "[Step 2/3] Extracting embedded images..."

        WORKDIR=$(mktemp -d "${TMPDIR:-/tmp}/xlsx2md.XXXXXX")
        trap "rm -rf '$WORKDIR'" EXIT

        # xlsx is a ZIP; images are in xl/media/
        unzip -o "$INPUT" "xl/media/*" -d "$WORKDIR" > /dev/null 2>&1 || true

        # Diagrams pasted into Excel from Visio/PowerPoint are stored as EMF,
        # which the OCR model cannot read; convert them to PNG like the docx
        # skill does, and say so when the converter is missing (silently
        # dropping them loses whole figures).
        EMF_COUNT=$(find "$WORKDIR" -name "*.emf" | wc -l)
        if [[ $EMF_COUNT -gt 0 ]]; then
            if command -v libreoffice >/dev/null 2>&1; then
                # -print0/read -d '' so a media name with a space is not split;
                # a conversion that yields no PNG is a lost figure, so warn.
                while IFS= read -r -d '' emf; do
                    libreoffice --headless --convert-to png "$emf" --outdir "$(dirname "$emf")" > /dev/null 2>&1 || true
                    [[ -f "${emf%.emf}.png" ]] || echo "  Warning: EMF→PNG conversion failed for $(basename "$emf"); figure skipped." >&2
                done < <(find "$WORKDIR" -name "*.emf" -print0)
            else
                echo "  Warning: $EMF_COUNT EMF image(s) present but LibreOffice not found; they are skipped." >&2
            fi
        fi

        PNG_COUNT=$(find "$WORKDIR" \( -name "*.png" -o -name "*.jpg" -o -name "*.jpeg" \) | wc -l)
        echo "  → Extracted $PNG_COUNT images to $WORKDIR"

        # Step 3: OCR each image
        echo "[Step 3/3] Running OCR on embedded images..."

        if [[ ! -x "$OCR_TO_MD" ]]; then
            echo "  Warning: OCR helper not found at '$OCR_TO_MD'." >&2
            echo "  Set OCR_TO_MD=/path/to/ocr_to_md.sh to enable OCR; skipping." >&2
        elif curl -fsS --max-time 3 "${OLLAMA_HOST:-http://localhost:11434}/api/tags" > /dev/null 2>&1 && \
           curl -fsS --max-time 3 "${OLLAMA_HOST:-http://localhost:11434}/api/tags" 2>/dev/null | \
           jq -e --arg m "${OCR_MODEL:-glm-ocr:bf16}" '.models[]?.name | select(. == $m)' > /dev/null 2>&1; then
            N=0
            TOTAL=$(find "$WORKDIR" \( -name "*.png" -o -name "*.jpg" -o -name "*.jpeg" \) | wc -l)

            while IFS= read -r -d '' img; do
                N=$((N + 1))
                BASENAME=$(basename "$img")
                OCR_OUTPUT="$WORKDIR/ocr_${BASENAME}.md"

                echo "  [${N}/${TOTAL}] OCR: $img" >&2
                # Judge by the produced text, not by exit status. Drop a blank
                # result so it cannot be appended as an empty "## Image:" section,
                # and warn (the skill verification keys on a clean stderr).
                "$OCR_TO_MD" --force "$img" "$OCR_OUTPUT" > /dev/null 2>&1 || true
                if [[ ! -s "$OCR_OUTPUT" ]] || [[ -z "$(tr -d '[:space:]' < "$OCR_OUTPUT")" ]]; then
                    rm -f "$OCR_OUTPUT"
                    echo "  Warning: OCR produced no text for $(basename "$img")" >&2
                fi
            done < <(find "$WORKDIR" \( -name "*.png" -o -name "*.jpg" -o -name "*.jpeg" \) -print0 | sort -z)

            # Append OCR results to output (only if images were processed)
            if [[ -d "$WORKDIR" ]]; then
                OCR_FILE_COUNT=$(find "$WORKDIR" -name "ocr_*.md" 2>/dev/null | wc -l)
            else
                OCR_FILE_COUNT=0
            fi

            if [[ $OCR_FILE_COUNT -gt 0 ]]; then
                {
                    echo ""
                    echo "---"
                    echo ""
                    echo "# Embedded Images (OCR'd)"
                    echo ""

                    while IFS= read -r -d '' ocr_file; do
                        IMG_NAME=$(basename "$ocr_file" .md | sed 's/^ocr_//')
                        echo "## Image: $IMG_NAME"
                        echo ""
                        cat "$ocr_file"
                        echo ""
                        echo "---"
                        echo ""
                    done < <(find "$WORKDIR" -name "ocr_*.md" -print0 | sort -z)
                } >> "${OUTPUT%.md}_temp.md"

                rm -rf "$WORKDIR"
            else
                if [[ -d "$WORKDIR" ]]; then
                    rm -rf "$WORKDIR"
                fi
            fi
        else
            if ! curl -fsS --max-time 3 "${OLLAMA_HOST:-http://localhost:11434}/api/tags" > /dev/null 2>&1; then
                echo "  Warning: Ollama not reachable, skipping OCR." >&2
            else
                echo "  Warning: OCR model ${OCR_MODEL:-glm-ocr:bf16} not found, skipping OCR." >&2
            fi
        fi
    fi
fi

# Final: move temp to output (always)
if [[ -f "${OUTPUT%.md}_temp.md" ]]; then
    mv "${OUTPUT%.md}_temp.md" "$OUTPUT"
fi

echo ""
echo "=== Conversion Complete ==="
if [[ -f "$OUTPUT" ]]; then
    echo "Output: $OUTPUT ($(wc -l < "$OUTPUT") lines, $(du -h "$OUTPUT" | cut -f1))"
else
    echo "(output written to stdout)"
fi
