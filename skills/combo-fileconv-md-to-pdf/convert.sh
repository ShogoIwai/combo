#!/bin/bash
# md-to-pdf/convert.sh — Convert Markdown to PDF via md-to-pdf (Chromium rendering).
# Japanese text, code blocks, tables and emoji render correctly (no LaTeX needed).
#
# Usage:
#   ./convert.sh input.md [output.pdf]
#   ./convert.sh docs/*.md                 # batch; each -> same-name .pdf
#   PDF_OPTIONS='{"format":"A4","margin":"15mm"}' ./convert.sh input.md
#   ./convert.sh --mermaid input.md out.pdf  # ```mermaid blocks drawn as diagrams (mmdc)
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

# Per-conversion limit: a headless Chromium that never exits must not hang the caller.
MD2PDF_TIMEOUT="${MD2PDF_TIMEOUT:-300}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --mermaid (anywhere in the arguments): render ```mermaid blocks to inline SVG first.
MERMAID=0
args=()
for x in "$@"; do
  if [ "$x" = "--mermaid" ]; then MERMAID=1; else args+=("$x"); fi
done
set -- "${args[@]+"${args[@]}"}"
if ! command -v md-to-pdf >/dev/null 2>&1; then
  echo "error: md-to-pdf not found. Install with: npm install -g md-to-pdf" >&2
  echo "       then: npx puppeteer browsers install chrome" >&2
  exit 1
fi

if [ "$#" -eq 0 ]; then
  echo "usage: $0 input.md [output.pdf]   |   $0 docs/*.md" >&2
  exit 2
fi

gen_pdf() {  # render $1 to a PDF in a private temp dir; echo that PDF's path
  # The renderer serves files from --basedir over a local HTTP server, so it gets
  # a private, freshly made directory holding only the staged copy: local images
  # are inlined as data: URIs (md_blocks.py stage), nothing beside the source is
  # served or written, and a read-only source directory works.
  local src_dir stage in rc=0
  src_dir="$(cd "$(dirname -- "$1")" && pwd)"
  stage="$(mktemp -d "${TMPDIR:-/tmp}/md2pdf.XXXXXX")"
  in="$stage/$(basename -- "${1%.*}").md"
  if [ "$MERMAID" = 1 ]; then
    python3 "$SCRIPT_DIR/md_prerender.py" "$1" "$stage/pre.md" 1>&2 || { rm -rf -- "$stage"; return 1; }
    python3 "$SCRIPT_DIR/md_blocks.py" stage "$stage/pre.md" "$in" "$src_dir" || rc=$?
    rm -f -- "$stage/pre.md"
  else
    python3 "$SCRIPT_DIR/md_blocks.py" stage "$1" "$in" "$src_dir" || rc=$?
  fi
  # (errexit does not reach inside a command substitution: check each step)
  [ "$rc" -eq 0 ] || { rm -rf -- "$stage"; return "$rc"; }
  # </dev/null: md-to-pdf always reads stdin and would convert piped input instead of the file
  timeout "$MD2PDF_TIMEOUT" md-to-pdf "$in" --basedir "$stage" --pdf-options "$PDF_OPTIONS" </dev/null 1>&2 || rc=$?
  rm -f -- "$in"
  if [ "$rc" -ne 0 ]; then
    rm -rf -- "$stage"
    [ "$rc" -eq 124 ] && echo "error: md-to-pdf did not exit within ${MD2PDF_TIMEOUT}s (MD2PDF_TIMEOUT)" >&2
    return "$rc"
  fi
  printf '%s\n' "${in%.*}.pdf"
}

publish() {  # move the staged PDF $1 to $2 and remove its private dir
  mv -f -- "$1" "$2"
  rm -rf -- "$(dirname -- "$1")"
}

# Two-arg form: explicit output filename (2nd arg ends in .pdf/.PDF).
if [ "$#" -eq 2 ] && printf '%s' "$2" | grep -qiE '\.pdf$'; then
  out_dir="$(dirname -- "$2")"
  [ -d "$out_dir" ] || mkdir -p -- "$out_dir"
  pdf="$(gen_pdf "$1")"   # (an assignment, so errexit stops on a failed conversion)
  publish "$pdf" "$2"
  echo "wrote: $2"
  exit 0
fi

# One-or-many inputs: each -> same-name .pdf.
for f in "$@"; do
  out="${f%.*}.pdf"   # same name as md-to-pdf's own default (extension swapped, any case)
  pdf="$(gen_pdf "$f")"
  publish "$pdf" "$out"
  echo "wrote: $out"
done
