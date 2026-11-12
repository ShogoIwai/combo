# combo-fileconv regression

Regression checks for `skills/combo-fileconv-*`, the frontier-harness
converters. Twins restricted to local LLMs and generic tools may exist
elsewhere; improvements land here first.

```bash
python3 <launch-root>/combo/tests/fileconv/run_regression.py [--out DIR] [--only docx,xlsx,pdf,md]
```

- The corpus is **local**: `corpus.local.tsv` (gitignored; or `--corpus FILE`,
  `FILECONV_CORPUS`) points at documents outside this repo. Their names and
  locations are internal information, so neither they nor any content is
  committed; `corpus.example.tsv` shows the format. Outputs go to `--out`
  (default `$TMPDIR/fileconv-regress`), never next to the source.
- Every check re-derives its expectation from the source **without the
  converter's code** (raw OOXML via zipfile/ElementTree, poppler for PDF), so a
  bug shared by converter and check cannot cancel out.
- `FILECONV_SKILLS=<dir>` runs the same checks against another skills tree
  (e.g. an old revision from `git archive`) to show what a change fixed.
- Each command is killed after `FILECONV_TIMEOUT` seconds (default 600) and
  counts as a FAIL, so a hang (e.g. a headless Chromium that never exits)
  cannot stall the run.
- Exit 0 when nothing FAILs. WARN marks known gaps that do not fail the run.

## Compatibility policy

1. **CLI defaults and flags keep their meaning.** New capabilities are opt-in
   flags; nothing existing is removed or reinterpreted.
2. **Default output may change only to fix a documented defect** (e.g. docx
   paragraph/table interleaving, TOC entries rendered as headings, dropped
   tracked insertions). Such changes are listed in the skill's SKILL.md.
3. **Raw and derived artifacts are separate files.** `--records` writes a
   separate JSONL; the Markdown is byte-identical with or without it.
4. Callers: `combo-transcripts-to-scqa` (docx-to-md), `combo-conference-report`
   (pdf-to-md OCR'd md as material), `combo-notion-to-md` (md-to-pdf
   downstream). Re-check them when a default changes.

## Provenance

Every `--records` file starts with a `meta` line: absolute source path, source
`sha256`, extractor script, `extractor_rev` (git short hash of combo,
`-dirty` when the skill dir has local changes) and library versions. Record
ids are source addresses (`b<N>`, `t<K>.r<I>.c<J>`, `cell:<sheet>!A1`,
`sheet:<name>`, `shape:<sheet>#<n>`, …) so later
consistency checks can point at the exact source location.

## Acceptance criteria per kind

| kind | checks (all from the source, independently) |
| --- | --- |
| docx | exit 0; table count; every table row has the header's column count; heading paragraphs (outline level from the paragraph or its style chain, else a `Heading N` style) and tables in source order; tracked row deletions excluded; every non-empty body paragraph and table (by its first cell) as an in-order subsequence of the Markdown; no TOC entry as a heading; every non-empty body paragraph's text present; records `sha256` and unique ids. Source text is the accepted view (no `w:del` / `w:moveFrom`) |
| xlsx | exit 0; no unreadable sheet; a section per sheet with data; table shape; records cell (formula or non-empty value) / formula / merged-range / comment counts equal the raw XML, comment parts found through each sheet's relationships; WARN on formulas without a cached result and on threaded comments (only their legacy copies are extracted). Layout mode: `xlsx_prepare.py` yields one sheet record per visible sheet (from `workbook.xml`) and a render each; the gate `xlsx_check.py` passes the deterministic draft and rejects a deleted cell identifier, an invented one and a rewired flowchart edge; WARN on connector networks that join no two shapes |
| pdf | exit 0; exactly one `## Page N` per page of the range, in order; WARN on pages without a text layer. Structured mode: `pdf_prepare.py` yields one page record per page and a render for every non-`text` page; the gate `pdf_check.py` passes the raw text as a trivially faithful conversion and rejects every corruption tried (a unique body identifier deleted, an invented register/value inserted) |
| md | exit 0; `%PDF-`; >= 1 page; headings present in the PDF text. With mermaid blocks: `--mermaid` exits 0, >= 97 % of the quoted label words (read from the Mermaid source, not the renderer) are in the PDF text, no diagram source is printed, `md_pdf_qa.py` passes, and a broken block fails naming its line; WARN only when mmdc is missing |

These are **consistency checks, not proof of fidelity**: they catch dropped,
reordered or misshapen content, not a value that moved to the wrong cell with
the same spelling.
