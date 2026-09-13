from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class DocumentKind(StrEnum):
    AUTOS_DO_PROCESSO = "autos_do_processo"
    CONTRATO = "contrato"
    EXTRATO = "extrato"
    COMPROVANTE_DE_CREDITO = "comprovante_de_credito"
    DOSSIE = "dossie"
    DEMONSTRATIVO_EVOLUCAO_DIVIDA = "demonstrativo_evolucao_divida"
    LAUDO_REFERENCIADO = "laudo_referenciado"


class StoredDocument(BaseModel):
    kind: DocumentKind
    original_filename: str
    content_type: str | None = None
    size_bytes: int = Field(ge=0)
    storage_path: Path


class PipelineIntakeResponse(BaseModel):
    case_id: str
    received_documents: list[StoredDocument]
    missing_documents: list[DocumentKind]
    status: str = "received"


class ExtractedDocument(BaseModel):
    kind: DocumentKind
    original_filename: str
    response_id: str
    extraction: dict[str, Any]


class PipelineAnalysisResponse(BaseModel):
    case_id: str
    model: str
    claims: list[dict[str, Any]]
    analysis: dict[str, Any]
    status: str = "completed"
