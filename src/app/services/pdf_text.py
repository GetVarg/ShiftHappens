"""Text-only PDF ingestion with page-level provenance.

This pipeline intentionally does not render pages or invoke OCR.  A page without a useful text
layer is reported to the caller as a limitation instead of being silently interpreted as an image.
"""

from __future__ import annotations

from dataclasses import dataclass

import fitz


MIN_TEXT_CHARS = 50


@dataclass(frozen=True)
class ExtractedPdfText:
    text: str
    page_count: int
    unreadable_pages: list[int]


def extract_pdf_text(content: bytes) -> ExtractedPdfText:
    try:
        document = fitz.open(stream=content, filetype="pdf")
    except (fitz.FileDataError, RuntimeError) as exc:
        raise ValueError("Não foi possível ler o conteúdo textual do PDF.") from exc

    pages: list[str] = []
    unreadable_pages: list[int] = []
    try:
        for page_number, page in enumerate(document, start=1):
            page_text = page.get_text("text", sort=True).strip()
            if len(page_text) < MIN_TEXT_CHARS:
                unreadable_pages.append(page_number)
                pages.append(f'<page number="{page_number}" text_status="unreadable"></page>')
            else:
                pages.append(f'<page number="{page_number}" text_status="available">\n{page_text}\n</page>')
    finally:
        document.close()

    if len(unreadable_pages) == len(pages):
        raise ValueError("O PDF não possui texto extraível; esta pipeline não usa OCR ou imagem.")
    return ExtractedPdfText(text="\n\n".join(pages), page_count=len(pages), unreadable_pages=unreadable_pages)
