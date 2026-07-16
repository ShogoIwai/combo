#!/bin/bash
# md-to-pdf/convert.sh — Convert Markdown to PDF via md-to-pdf (Chromium rendering).
# Japanese text, code blocks, tables and emoji render correctly (no LaTeX needed).
#
# Usage:
#   ./convert.sh input.md [output.pdf]
#   ./convert.sh docs/*.md                 # batch; each -> same-name .pdf
#   PDF_OPTIONS='{"format":"A4","margin":"15mm"}' ./convert.sh input.md
#
# Prereqs (one-time, see SKILL.md):
#   npm install -g md-to-pdf
#   npx puppeteer browsers install chrome   # downloads Chromium into ~/.cache/puppeteer
set -euo pipefail

# Always pass --pdf-options. Callers may override via the PDF_OPTIONS env var;
# otherwise this A4 / 15mm-margin default is applied on every conversion.
# (Assigned in two steps: a JSON default inside ${:=} would have its closing
#  brace swallowed by the parameter-expansion syntax.)
PDF_OPTIONS="${PDF_OPTIONS:-}"
[ -n "$PDF_OPTIONS" ] || PDF_OPTIONS='{"format":"A4","margin":"15mm"}'

if ! command -v md-to-pdf >/dev/null 2>&1; then
  echo "error: md-to-pdf not found. Install with: npm install -g md-to-pdf" >&2
  echo "       then: npx puppeteer browsers install chrome" >&2
  exit 1
fi

if [ "$#" -eq 0 ]; then
  echo "usage: $0 input.md [output.pdf]   |   $0 docs/*.md" >&2
  exit 2
fi

gen_pdf() {  # run md-to-pdf on $1, echo the file it produced (extension replaced with .pdf)
  md-to-pdf "$1" --pdf-options "$PDF_OPTIONS" 1>&2   # PDF_OPTIONS always set; keep progress off stdout
  printf '%s\n' "${1%.*}.pdf"   # matches md-to-pdf's path.parse() extension swap (any case)
}

# Two-arg form: explicit output filename (2nd arg ends in .pdf/.PDF).
if [ "$#" -eq 2 ] && printf '%s' "$2" | grep -qiE '\.pdf$'; then
  src_pdf="$(gen_pdf "$1")"
  out_dir="$(dirname -- "$2")"
  [ -d "$out_dir" ] || mkdir -p -- "$out_dir"
  # skip the move when source and destination resolve to the same file (e.g. ./x.pdf vs x.pdf)
  [ "$src_pdf" -ef "$2" ] || mv -f -- "$src_pdf" "$2"
  echo "wrote: $2"
  exit 0
fi

# One-or-many inputs: each -> same-name .pdf.
for f in "$@"; do
  out="$(gen_pdf "$f")"
  echo "wrote: $out"
done
