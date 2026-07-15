---
name: combo-docx-to-md
description: Convert .docx design specs to markdown with OCR for embedded images
---
# docx-to-md Skill

Convert `.docx` design specification documents to clean Markdown format.
Handles embedded images via OCR when Ollama + GLM-OCR is available.

## When to Use

- Converting vendor design specs to searchable markdown
- Extracting text content from `.docx` files for review or summarization
- Processing documents with embedded diagrams/tables that need OCR

## Prerequisites

1. **python-docx** — for structured extraction (headings, tables)
2. **Ollama + glm-ocr model** — optional, for image OCR (`ollama pull glm-ocr:bf16`)

## Usage

```bash
# Basic conversion (text only)
<launch-root>/combo/skills/combo-docx-to-md/convert.sh input.docx [output.md]

# With OCR for embedded images (requires Ollama running)
<launch-root>/combo/skills/combo-docx-to-md/convert.sh input.docx --ocr [output.md]
```

## Workflow

1. **Extract text** using python-docx with proper heading hierarchy
2. **Extract images** from `word/media/` in the docx (ZIP format)
3. **Convert EMF → PNG** via LibreOffice if needed
4. **OCR each image** page-by-page using Ollama + GLM-OCR
5. **Combine** text + OCR results into final markdown

## Output Structure

The output `.md` file contains:

- Original document structure (headings, paragraphs)
- Tables in markdown format
- OCR'd image content at the end (if images were present)

## Notes

- EMF files require LibreOffice for conversion to PNG
- Ollama must be running with `glm-ocr:bf16` model pulled
- Re-running overwrites the output `.md` in place
