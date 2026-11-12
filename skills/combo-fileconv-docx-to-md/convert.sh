#!/bin/bash
# convert.sh — Convert .docx to markdown with optional OCR for embedded images.
#
# Usage:
#   ./convert.sh <input.docx> [output.md] [--ocr] [--records out.jsonl]
#
# Options:
#   --ocr              Enable OCR for embedded images (requires Ollama + glm-ocr)
#   --records FILE     Also write source-addressed records (JSONL) for consistency checks
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
RECORDS=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --ocr) ENABLE_OCR=1; shift ;;
        --records)
            [[ -n "${2-}" && "${2-}" != --* ]] || { echo "Error: --records requires a file path" >&2; exit 1; }
            RECORDS="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: $0 <input.docx> [output.md] [--ocr] [--records out.jsonl]"
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

# Body walk in document order (see docx_to_md.py for the text/heading rules).
# stderr is left visible so a python-docx exception shows up in the log.
python3 "$SCRIPT_DIR/docx_to_md.py" "$INPUT" "${OUTPUT%.md}_temp.md" ${RECORDS:+--records "$RECORDS"}

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

    PNG_COUNT=$(find "$WORKDIR" \( -name "*.png" -o -name "*.jpg" -o -name "*.jpeg" \) | wc -l)
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
        TOTAL=$(find "$WORKDIR" \( -name "*.png" -o -name "*.jpg" -o -name "*.jpeg" \) | wc -l)

        while IFS= read -r -d '' img; do
            N=$((N + 1))
            BASENAME=$(basename "$img")
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
        done < <(find "$WORKDIR" \( -name "*.png" -o -name "*.jpg" -o -name "*.jpeg" \) -print0 | sort -z)

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
