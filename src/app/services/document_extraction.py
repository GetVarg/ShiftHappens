"""OpenAI-backed extraction of one PDF into the common pipeline contract."""

import json
from dataclasses import dataclass

from fastapi import UploadFile
from openai import APIError, AsyncOpenAI

from app.models.documents import DocumentKind
from app.prompts.document_extraction import (
    build_autos_claim_prompt,
    build_targeted_document_prompt,
    get_autos_claim_schema,
    get_targeted_document_schema,
)
from app.prompts.flag_classification import (
    build_flag_classification_prompt,
    get_flag_candidate_schema,
)
from app.services.pdf_text import extract_pdf_text


PDF_SIGNATURE = b"%PDF"
MAX_PDF_BYTES = 50 * 1024 * 1024


class DocumentExtractionError(RuntimeError):
    """A recoverable failure while the OpenAI API processes a document."""


@dataclass(frozen=True)
class ExtractionResult:
    response_id: str
    output: dict


def _parse_structured_output(response: object) -> dict:
    """Parse a completed response without disclosing document content on errors."""
    status = getattr(response, "status", None)
    if status != "completed":
        error = getattr(response, "error", None)
        incomplete = getattr(response, "incomplete_details", None)
        raise DocumentExtractionError(
            f"A OpenAI não concluiu a resposta estruturada (status={status}; "
            f"erro={error}; incompleta={incomplete})."
        )

    output_text = (getattr(response, "output_text", None) or "").strip()
    if output_text.startswith("```json") and output_text.endswith("```"):
        output_text = output_text[7:-3].strip()
    if not output_text:
        content_types = [
            getattr(content, "type", "unknown")
            for item in getattr(response, "output", [])
            for content in getattr(item, "content", [])
        ]
        raise DocumentExtractionError(
            "A OpenAI concluiu sem texto JSON "
            f"(tipos de conteúdo: {', '.join(content_types) or 'nenhum'})."
        )
    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise DocumentExtractionError(
            "A OpenAI concluiu com resposta não interpretável como JSON "
            f"(caracteres recebidos: {len(output_text)}; posição inválida: {exc.pos})."
        ) from exc
    if not isinstance(parsed, dict):
        raise DocumentExtractionError("A OpenAI retornou JSON estruturado com raiz diferente de objeto.")
    return parsed


class DocumentExtractionClient:
    def __init__(self, api_key: str | None, model: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key) if api_key else None
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    async def extract_claims(
        self,
        *,
        case_id: str,
        upload: UploadFile,
    ) -> ExtractionResult:
        return await self._extract(
            case_id=case_id,
            kind=DocumentKind.AUTOS_DO_PROCESSO,
            upload=upload,
            instructions=build_autos_claim_prompt(
                case_id=case_id, filename=upload.filename or "autos_do_processo.pdf"
            ),
            schema_name="legal_case_claims",
            schema=get_autos_claim_schema(),
            output_hint="Identifique as afirmações verificáveis dos autos anexados.",
        )

    async def extract_targeted_evidence(
        self,
        *,
        case_id: str,
        kind: DocumentKind,
        upload: UploadFile,
        claims: list[dict],
    ) -> ExtractionResult:
        return await self._extract(
            case_id=case_id,
            kind=kind,
            upload=upload,
            instructions=build_targeted_document_prompt(
                kind=kind, filename=upload.filename or f"{kind.value}.pdf", claims=claims
            ),
            schema_name="targeted_legal_evidence",
            schema=get_targeted_document_schema(),
            output_hint="Procure exclusivamente evidências para as afirmações recebidas.",
        )

    async def classify_claim_flag(
        self, *, claim: dict, evidence_catalog: list[dict]
    ) -> ExtractionResult:
        """Classify one claim with its compact evidence; PDFs are not sent a second time."""
        if self._client is None:
            raise RuntimeError("OPENAI_API_KEY nao configurada.")
        try:
            response = await self._client.responses.create(
                model=self._model,
                instructions=build_flag_classification_prompt(
                    claim=claim, evidence_catalog=evidence_catalog
                ),
                input="Decida se esta claim produz uma flag relevante para o banco.",
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "claim_legal_flag",
                        "strict": True,
                        "schema": get_flag_candidate_schema(),
                    }
                },
                store=False,
            )
        except APIError as exc:
            status = getattr(exc, "status_code", None)
            prefix = f"OpenAI respondeu {status}" if status else "Falha de comunicação com a OpenAI"
            raise DocumentExtractionError(
                f"{prefix} durante a classificação da flag da claim '{claim.get('claim_id')}': {exc}."
            ) from exc
        return ExtractionResult(response_id=response.id, output=_parse_structured_output(response))

    async def _extract(
        self,
        *,
        case_id: str,
        kind: DocumentKind,
        upload: UploadFile,
        instructions: str,
        schema_name: str,
        schema: dict,
        output_hint: str,
    ) -> ExtractionResult:
        if self._client is None:
            raise RuntimeError("OPENAI_API_KEY nao configurada.")

        filename = upload.filename or f"{kind.value}.pdf"
        if not filename.lower().endswith(".pdf"):
            raise ValueError(f"{kind.value}: o arquivo precisa estar em PDF.")

        content = await upload.read()
        if not content.startswith(PDF_SIGNATURE):
            raise ValueError(f"{kind.value}: o conteúdo enviado não parece ser um PDF válido.")
        if len(content) >= MAX_PDF_BYTES:
            raise ValueError(
                f"{kind.value}: o PDF precisa ter menos de 50 MB para análise."
            )

        extracted_text = extract_pdf_text(content)
        operation = "análise do texto extraído"
        try:
            response = await self._client.responses.create(
                model=self._model,
                instructions=instructions,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    f"{output_hint}\n\n"
                                    "TEXTO EXTRAÍDO LOCALMENTE, COM MARCAÇÃO DE PÁGINA:\n"
                                    f"{extracted_text.text}"
                                ),
                            },
                        ],
                    }
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    }
                },
                store=False,
            )
        except APIError as exc:
            status = getattr(exc, "status_code", None)
            prefix = f"OpenAI respondeu {status}" if status else "Falha de comunicação com a OpenAI"
            root_cause = exc.__cause__
            cause_detail = (
                f" Causa técnica: {type(root_cause).__name__}: {root_cause}"
                if root_cause is not None
                else ""
            )
            size_mb = len(content) / (1024 * 1024)
            raise DocumentExtractionError(
                f"{prefix} durante {operation} de '{filename}' ({size_mb:.2f} MB): "
                f"{exc}.{cause_detail}"
            ) from exc
        output = _parse_structured_output(response)

        if extracted_text.unreadable_pages:
            note = (
                "Páginas sem texto extraível (modo texto sem OCR/imagem): "
                + ", ".join(str(page) for page in extracted_text.unreadable_pages)
            )
            output.setdefault("missing_evidence", []).append(note)
        return ExtractionResult(response_id=response.id, output=output)
