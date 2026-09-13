import re
from pathlib import Path

from fastapi import UploadFile

from app.models.documents import DocumentKind, StoredDocument


PDF_SIGNATURE = b"%PDF"


def _safe_filename(filename: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename.strip())
    return normalized or "document.pdf"


async def save_pdf_upload(
    upload: UploadFile,
    kind: DocumentKind,
    target_dir: Path,
) -> StoredDocument:
    filename = upload.filename or f"{kind.value}.pdf"
    if not filename.lower().endswith(".pdf"):
        raise ValueError(f"{kind.value}: o arquivo precisa estar em PDF.")

    content = await upload.read()
    if not content.startswith(PDF_SIGNATURE):
        raise ValueError(f"{kind.value}: o conteúdo enviado não parece ser um PDF válido.")

    target_dir.mkdir(parents=True, exist_ok=True)
    output_path = target_dir / f"{kind.value}__{_safe_filename(filename)}"
    output_path.write_bytes(content)

    return StoredDocument(
        kind=kind,
        original_filename=filename,
        content_type=upload.content_type,
        size_bytes=len(content),
        storage_path=output_path,
    )
