#!/bin/bash
# convert.sh — Convert .docx to markdown with optional OCR for embedded images.
#
# Usage:
#   ./convert.sh <input.docx> [output.md] [--ocr]
#
# Options:
#   --ocr    Enable OCR for embedded images (requires Ollama + glm-ocr)
#
# Requires:
#   - python-docx (pip install python-docx)
#   - Ollama with glm-ocr model — only if --ocr is used

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# OCR helper: the shared ollama/ocr_to_md.sh (sibling of combo/ in the workspace).
# Override with OCR_TO_MD if it lives elsewhere.
OCR_TO_MD="${OCR_TO_MD:-$SCRIPT_DIR/../../../ollama/ocr_to_md.sh}"

INPUT=""
OUTPUT=""
ENABLE_OCR=0

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --ocr) ENABLE_OCR=1; shift ;;
        --help|-h)
            echo "Usage: $0 <input.docx> [output.md] [--ocr]"
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
    echo "Error: input .docx file required" >&2
    exit 1
fi

if [[ ! -f "$INPUT" ]]; then
    echo "Error: input file not found: $INPUT" >&2
    exit 1
fi

# Default output: same dir, change extension to .md
OUTPUT="${OUTPUT:-${INPUT%.*}.md}"

echo "=== docx-to-md Converter ==="
echo "Input:  $INPUT"
echo "Output: $OUTPUT"
echo "OCR:    $(if [[ $ENABLE_OCR -eq 1 ]]; then echo 'enabled'; else echo 'disabled (text only)'; fi)"
echo ""

# Step 1: Extract text using python-docx
echo "[Step 1/3] Extracting text with python-docx..."

python3 -c "
from docx import Document
import re
import sys

def get_heading_level(para):
    style_name = para.style.name if para.style else ''
    match = re.match(r'toc\s+(\d+)', style_name, re.IGNORECASE)
    if match: return int(match.group(1))
    match = re.match(r'heading\s+(\d+)', style_name, re.IGNORECASE)
    if match: return int(match.group(1))
    return None

def heading_to_md(text, level):
    prefix = '#' * min(level, 6)
    return f'{prefix} {text}\n\n'

def table_to_md(table):
    lines = []
    for i, row in enumerate(table.rows):
        cells = [cell.text.strip() for cell in row.cells]
        if i == 0:
            lines.append('| ' + ' | '.join(cells) + ' |')
            lines.append('| ' + ' | '.join(['---'] * len(cells)) + ' |')
        else:
            lines.append('| ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines) + '\n\n'

doc = Document(sys.argv[1])
output_lines = []
skip_next_empty = False

for para in doc.paragraphs:
    style_name = para.style.name if para.style else ''
    text = para.text.strip()
    level = get_heading_level(para)

    if level is not None and text:
        output_lines.append(heading_to_md(text, level))
        skip_next_empty = False
    elif text:
        if text.startswith('•') or text.startswith('-'):
            output_lines.append(f'{text}\n\n')
        else:
            output_lines.append(f'{text}\n\n')
        skip_next_empty = False
    elif not skip_next_empty and style_name == 'Normal':
        skip_next_empty = True

# Add tables
for table in doc.tables:
    output_lines.append(table_to_md(table))

md_content = ''.join(output_lines).rstrip() + '\n'
sys.stdout.write(md_content)
" "$INPUT" > "${OUTPUT%.md}_temp.md" 2>/dev/null

echo "  → Text extraction complete ($(wc -l < "${OUTPUT%.md}_temp.md") lines)"

# Step 2: Extract images (if OCR enabled)
if [[ $ENABLE_OCR -eq 1 ]]; then
    echo ""
    echo "[Step 2/3] Extracting embedded images..."

    WORKDIR=$(mktemp -d "${TMPDIR:-/tmp}/docx2md.XXXXXX")
    trap "rm -rf '$WORKDIR'" EXIT

    # unzip returns non-zero when the archive has no word/media/* entries
    # (text-only docx); tolerate it so --ocr does not abort under set -e.
    unzip -o "$INPUT" "word/media/*" -d "$WORKDIR" > /dev/null 2>&1 || true

    # Convert EMF to PNG via LibreOffice
    for emf in "$WORKDIR"/word/media/*.emf; do
        [[ -f "$emf" ]] || continue
        libreoffice --headless --convert-to png "$emf" --outdir "$(dirname "$emf")" > /dev/null 2>&1 || true
        # a conversion that yields no PNG is a lost figure, so say so
        [[ -f "${emf%.emf}.png" ]] || echo "  Warning: EMF→PNG conversion failed for $(basename "$emf"); figure skipped." >&2
    done

    PNG_COUNT=$(find "$WORKDIR" -name "*.png" | wc -l)
    echo "  → Extracted $PNG_COUNT images to $WORKDIR"

    # Step 3: OCR each image
    echo "[Step 3/3] Running OCR on extracted images..."

    if [[ ! -x "$OCR_TO_MD" ]]; then
        echo "  Warning: OCR helper not found at '$OCR_TO_MD'." >&2
        echo "  Set OCR_TO_MD=/path/to/ocr_to_md.sh to enable OCR; skipping." >&2
    elif curl -fsS --max-time 3 "${OLLAMA_HOST:-http://localhost:11434}/api/tags" > /dev/null 2>&1 && \
       curl -fsS --max-time 3 "${OLLAMA_HOST:-http://localhost:11434}/api/tags" 2>/dev/null | \
       jq -e --arg m "${OCR_MODEL:-glm-ocr:bf16}" '.models[]?.name | select(. == $m)' > /dev/null 2>&1; then
        N=0
        TOTAL=$(find "$WORKDIR" -name "*.png" | wc -l)

        while IFS= read -r -d '' img; do
            N=$((N + 1))
            BASENAME=$(basename "$img" .png)
            OCR_OUTPUT="$WORKDIR/ocr_${BASENAME}.md"

            echo "  [${N}/${TOTAL}] OCR: $img" >&2
            # Judge by the produced text, not by exit status. Drop a blank result
            # so it cannot be appended as an empty "## Figure:" section, and warn
            # (the skill verification keys on a clean stderr).
            "$OCR_TO_MD" --force "$img" "$OCR_OUTPUT" > /dev/null 2>&1 || true
            if [[ ! -s "$OCR_OUTPUT" ]] || [[ -z "$(tr -d '[:space:]' < "$OCR_OUTPUT")" ]]; then
                rm -f "$OCR_OUTPUT"
                echo "  Warning: OCR produced no text for $(basename "$img")" >&2
            fi
        done < <(find "$WORKDIR" -name "*.png" -print0 | sort -z)

        OCR_FILE_COUNT=$(find "$WORKDIR" -maxdepth 1 -name "ocr_*.md" 2>/dev/null | wc -l)
        if [[ $OCR_FILE_COUNT -eq 0 ]]; then
            if [[ $TOTAL -eq 0 ]]; then
                echo "  → No OCR-able images in this document; nothing appended."
            else
                echo "  Warning: no image yielded OCR text; nothing appended." >&2
            fi
        else

        # Append OCR results to output
        {
            echo ""
            echo "---"
            echo ""
            echo "# Embedded Images (OCR'd)"
            echo ""

            while IFS= read -r -d '' ocr_file; do
                IMG_NAME=$(basename "$ocr_file" .md | sed 's/^ocr_//')
                echo "## Figure: $IMG_NAME"
                echo ""
                cat "$ocr_file"
                echo ""
                echo "---"
                echo ""
            done < <(find "$WORKDIR" -maxdepth 1 -name "ocr_*.md" -print0 | sort -z)
        } >> "${OUTPUT%.md}_temp.md"
        fi

        rm -rf "$WORKDIR"
    else
        if ! curl -fsS --max-time 3 "${OLLAMA_HOST:-http://localhost:11434}/api/tags" > /dev/null 2>&1; then
            echo "  Warning: Ollama not reachable, skipping OCR." >&2
        else
            echo "  Warning: OCR model ${OCR_MODEL:-glm-ocr:bf16} not found, skipping OCR." >&2
        fi
    fi
fi

# Final: move temp to output (always)
if [[ -f "${OUTPUT%.md}_temp.md" ]]; then
    mv "${OUTPUT%.md}_temp.md" "$OUTPUT"
fi

echo ""
echo "=== Conversion Complete ==="
echo "Output: $OUTPUT ($(wc -l < "$OUTPUT") lines, $(du -h "$OUTPUT" | cut -f1))"
