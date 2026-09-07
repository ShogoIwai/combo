#!/bin/bash
# convert.sh — Convert a .pdf to Markdown, page by page, with optional OCR.
#
# Usage:
#   ./convert.sh <input.pdf> [output.md] [--ocr|--ocr-fallback] [--pages N-M] [--password PW]
#
# Modes:
#   (default)        text layer only, via pdftotext -layout
#   --ocr-fallback   text layer, but OCR any page whose text layer is empty
#   --ocr            OCR every page, ignoring the text layer (scanned PDFs)
#
# Requires:
#   - poppler-utils (pdftotext, pdfinfo; pdftoppm for OCR modes)
#   - OCR modes only: ocr_to_md.sh (see OCR_TO_MD) + Ollama with glm-ocr
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# OCR helper: the shared ollama/ocr_to_md.sh (sibling of combo/ in the workspace).
# Override with OCR_TO_MD if it lives elsewhere.
OCR_TO_MD="${OCR_TO_MD:-$SCRIPT_DIR/../../../ollama/ocr_to_md.sh}"

INPUT=""; OUTPUT=""; OCR_MODE="none"; PAGES=""; PASSWORD=""

# --ocr and --ocr-fallback are different documents, not a preference: taking the
# last one silently would make the mode depend on flag order.
set_ocr_mode() {
    if [[ "$OCR_MODE" != "none" ]] && [[ "$OCR_MODE" != "$1" ]]; then
        echo "Error: --ocr and --ocr-fallback are mutually exclusive" >&2; exit 1
    fi
    OCR_MODE="$1"
}
# An option whose value is missing must say so; a bare `shift 2` would abort
# under `set -e` with no message at all. The lookahead also rejects the next
# option as a value, so `--pages --ocr` is reported as a missing value rather
# than swallowing --ocr and failing later as a malformed range.
need_value() {
    local opt="$1" next="${2-}"
    case "$next" in
        "" | --ocr | --ocr-fallback | --pages | --password | --help | -h)
            echo "Error: $opt requires a value" >&2; exit 1 ;;
    esac
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --ocr) set_ocr_mode "all"; shift ;;
        --ocr-fallback) set_ocr_mode "fallback"; shift ;;
        --pages) need_value --pages "${2-}"; PAGES="$2"; shift 2 ;;
        --password) need_value --password "${2-}"; PASSWORD="$2"; shift 2 ;;
        --help|-h) echo "Usage: $0 <input.pdf> [output.md] [--ocr|--ocr-fallback] [--pages N-M] [--password PW]"; exit 0 ;;
        *)
            if [[ -z "$INPUT" ]]; then INPUT="$1"
            elif [[ -z "$OUTPUT" ]]; then OUTPUT="$1"; fi
            shift ;;
    esac
done

[[ -n "$INPUT" ]] || { echo "Error: input .pdf file required" >&2; exit 1; }
[[ -f "$INPUT" ]] || { echo "Error: input file not found: $INPUT" >&2; exit 1; }
command -v pdftotext >/dev/null || { echo "Error: pdftotext (poppler-utils) not found" >&2; exit 1; }
command -v pdfinfo   >/dev/null || { echo "Error: pdfinfo (poppler-utils) not found" >&2; exit 1; }

OUTPUT="${OUTPUT:-${INPUT%.*}.md}"

# Never let the output land on the input: the final mv would replace the PDF.
if [[ -e "$OUTPUT" ]] && [[ "$OUTPUT" -ef "$INPUT" ]]; then
    echo "Error: output would overwrite the input PDF ($INPUT)" >&2; exit 1
fi

PDF_PW_ARGS=()
if [[ -n "$PASSWORD" ]]; then PDF_PW_ARGS+=(-upw "$PASSWORD"); fi

INFO=$(pdfinfo "${PDF_PW_ARGS[@]+"${PDF_PW_ARGS[@]}"}" "$INPUT" 2>/dev/null) || INFO=""
TOTAL=$(printf '%s\n' "$INFO" | awk '/^Pages:/ {print $2}')
[[ -n "${TOTAL:-}" ]] || { echo "Error: cannot read page count (corrupt PDF?)" >&2; exit 1; }
# pdfinfo still reports a page count for an encrypted PDF, so reject it here
# rather than emit a document of silently empty pages — unless a password was
# given, in which case every poppler call below carries it.
if [[ -z "$PASSWORD" ]] && printf '%s\n' "$INFO" | grep -q '^Encrypted:[[:space:]]*yes'; then
    echo "Error: PDF is encrypted; use --password or decrypt with qpdf --decrypt" >&2; exit 1
fi

FIRST=1; LAST="$TOTAL"
if [[ -n "$PAGES" ]]; then
    FIRST="${PAGES%%-*}"; LAST="${PAGES##*-}"
    [[ "$FIRST" =~ ^[0-9]+$ && "$LAST" =~ ^[0-9]+$ && "$FIRST" -ge 1 && "$LAST" -le "$TOTAL" && "$FIRST" -le "$LAST" ]] \
        || { echo "Error: bad --pages '$PAGES' (document has $TOTAL pages)" >&2; exit 1; }
fi

echo "=== pdf-to-md Converter ==="
echo "Input:  $INPUT ($TOTAL pages)"
echo "Output: $OUTPUT"
echo "Range:  $FIRST-$LAST"
echo "OCR:    $OCR_MODE"
echo ""

if [[ "$OCR_MODE" != "none" ]]; then
    command -v pdftoppm >/dev/null || { echo "Error: pdftoppm needed for OCR modes" >&2; exit 1; }
    if [[ ! -x "$OCR_TO_MD" ]]; then
        echo "Warning: OCR helper not found at '$OCR_TO_MD'." >&2
        echo "Warning: set OCR_TO_MD=/path/to/ocr_to_md.sh to enable OCR; OCR disabled." >&2
        OCR_MODE="none"
    elif ! curl -fsS --max-time 3 "${OLLAMA_HOST:-http://localhost:11434}/api/tags" >/dev/null 2>&1; then
        echo "Warning: Ollama not reachable; OCR disabled." >&2
        OCR_MODE="none"
    elif ! curl -fsS --max-time 3 "${OLLAMA_HOST:-http://localhost:11434}/api/tags" 2>/dev/null \
         | jq -e --arg m "${OCR_MODEL:-glm-ocr:bf16}" '.models[]?.name | select(. == $m)' >/dev/null 2>&1; then
        echo "Warning: OCR model ${OCR_MODEL:-glm-ocr:bf16} not found; OCR disabled." >&2
        OCR_MODE="none"
    fi
fi

WORKDIR=$(mktemp -d "${TMPDIR:-/tmp}/pdf2md.XXXXXX")
trap 'rm -rf "$WORKDIR"' EXIT
TMP_OUT="$WORKDIR/out.md"

TITLE=$(printf '%s\n' "$INFO" | sed -n 's/^Title:[[:space:]]*//p' | head -1)
{
    echo "# ${TITLE:-$(basename "${INPUT%.*}")}"
    echo ""
    echo "> Source: \`$(basename "$INPUT")\` — pages $FIRST-$LAST of $TOTAL"
    echo ""
} > "$TMP_OUT"

OCR_PAGES=0
for ((p=FIRST; p<=LAST; p++)); do
    TXT="$WORKDIR/page_$p.txt"
    if [[ "$OCR_MODE" == "all" ]]; then
        : > "$TXT"
    elif ! pdftotext "${PDF_PW_ARGS[@]+"${PDF_PW_ARGS[@]}"}" -layout -f "$p" -l "$p" "$INPUT" "$TXT" 2>/dev/null; then
        # a page pdftotext cannot read is a real fault, not an empty page
        echo "  Warning: pdftotext failed on page $p" >&2
        : > "$TXT"
    fi

    # whitespace/form-feed only counts as empty in every mode
    HAS_TEXT=0
    [[ -s "$TXT" ]] && [[ -n "$(tr -d '[:space:]\f' < "$TXT")" ]] && HAS_TEXT=1

    USED_OCR=0
    if [[ "$OCR_MODE" == "all" ]] || { [[ "$OCR_MODE" == "fallback" ]] && [[ $HAS_TEXT -eq 0 ]]; }; then
        if [[ "$OCR_MODE" != "none" ]]; then
            echo "  [page $p/$LAST] OCR..." >&2
            pdftoppm "${PDF_PW_ARGS[@]+"${PDF_PW_ARGS[@]}"}" -r "${OCR_DPI:-200}" -png -f "$p" -l "$p" -singlefile "$INPUT" "$WORKDIR/page_$p" >/dev/null 2>&1 || true
            if [[ -f "$WORKDIR/page_$p.png" ]]; then
                # judge by the produced text, not by exit status: the OCR helper
                # can write a good page and still exit non-zero
                "$OCR_TO_MD" --force "$WORKDIR/page_$p.png" "$WORKDIR/ocr_$p.md" >/dev/null 2>&1 || true
                if [[ -s "$WORKDIR/ocr_$p.md" ]] && [[ -n "$(tr -d '[:space:]' < "$WORKDIR/ocr_$p.md")" ]]; then
                    cp "$WORKDIR/ocr_$p.md" "$TXT"; USED_OCR=1; HAS_TEXT=1; OCR_PAGES=$((OCR_PAGES+1))
                else
                    echo "  Warning: OCR failed on page $p" >&2
                fi
            else
                echo "  Warning: could not rasterize page $p" >&2
            fi
        fi
    fi

    {
        echo "## Page $p"
        echo ""
        if [[ $HAS_TEXT -eq 1 ]]; then
            if [[ $USED_OCR -eq 1 ]]; then
                cat "$TXT"
            else
                # Keep the fixed-layout text (only the form feed and trailing
                # blanks go): tables/columns survive only inside a code fence.
                # The fence is made longer than the longest backtick run in the
                # page, so page text containing ``` cannot close it early.
                # A closing fence may be indented by up to 3 spaces, so the
                # scan allows that indent and strips it before measuring.
                # grep exits 1 when a page has no backticks at all; under
                # `set -e` that status would abort the run, hence the `|| true`.
                MAXTICKS=$( { grep -oE '^[[:space:]]{0,3}`+' "$TXT" || true; } \
                    | sed 's/^[[:space:]]*//' \
                    | awk '{ if (length($0) > n) n = length($0) } END { print n+0 }')
                FENCE=$(printf '`%.0s' $(seq $(( MAXTICKS >= 3 ? MAXTICKS + 1 : 3 ))))
                echo "${FENCE}text"
                sed -e 's/\f//g' -e 's/[[:space:]]*$//' "$TXT"
                echo "$FENCE"
            fi
        else
            echo "_(no extractable text on this page)_"
        fi
        echo ""
    } >> "$TMP_OUT"
done

mv "$TMP_OUT" "$OUTPUT"

echo ""
echo "=== Conversion Complete ==="
echo "Output: $OUTPUT ($(wc -l < "$OUTPUT") lines, $(du -h "$OUTPUT" | cut -f1)), OCR'd pages: $OCR_PAGES"
