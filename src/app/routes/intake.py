import asyncio
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.config import get_settings
from app.models.documents import (
    DocumentKind,
    PipelineAnalysisResponse,
    PipelineIntakeResponse,
    StoredDocument,
)
from app.services.document_storage import save_pdf_upload
from app.services.document_extraction import (
    DocumentExtractionClient,
    DocumentExtractionError,
)
from app.services.openai_chat import get_document_extraction_client
from app.services.claim_assessment import build_case_assessment
from app.services.flag_scoring import build_claim_flag_catalogs, score_and_rank_flags


router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.post(
    "/intake",
    response_model=PipelineIntakeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def intake_documents(
    case_id: Annotated[str | None, Form()] = None,
    autos_do_processo: Annotated[UploadFile | None, File()] = None,
    contrato: Annotated[UploadFile | None, File()] = None,
    extrato: Annotated[UploadFile | None, File()] = None,
    comprovante_de_credito: Annotated[UploadFile | None, File()] = None,
    dossie: Annotated[UploadFile | None, File()] = None,
    demonstrativo_evolucao_divida: Annotated[UploadFile | None, File()] = None,
    laudo_referenciado: Annotated[UploadFile | None, File()] = None,
) -> PipelineIntakeResponse:
    """
    Receives the optional PDF documents that start the agreement-policy pipeline.

    The front-end can send any subset of the supported files as multipart/form-data.
    """
    resolved_case_id = case_id or f"case-{uuid4().hex}"
    upload_dir = get_settings().upload_dir / resolved_case_id

    incoming_files: dict[DocumentKind, UploadFile | None] = {
        DocumentKind.AUTOS_DO_PROCESSO: autos_do_processo,
        DocumentKind.CONTRATO: contrato,
        DocumentKind.EXTRATO: extrato,
        DocumentKind.COMPROVANTE_DE_CREDITO: comprovante_de_credito,
        DocumentKind.DOSSIE: dossie,
        DocumentKind.DEMONSTRATIVO_EVOLUCAO_DIVIDA: demonstrativo_evolucao_divida,
        DocumentKind.LAUDO_REFERENCIADO: laudo_referenciado,
    }

    received_documents: list[StoredDocument] = []
    for kind, upload in incoming_files.items():
        if upload is None:
            continue

        try:
            received_documents.append(await save_pdf_upload(upload, kind, upload_dir))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    missing_documents = [
        kind for kind, upload in incoming_files.items() if upload is None
    ]

    return PipelineIntakeResponse(
        case_id=resolved_case_id,
        received_documents=received_documents,
        missing_documents=missing_documents,
    )


@router.post("/analyze", response_model=PipelineAnalysisResponse)
async def analyze_case_documents(
    case_id: Annotated[str | None, Form()] = None,
    autos_do_processo: Annotated[UploadFile | None, File()] = None,
    contrato: Annotated[UploadFile | None, File()] = None,
    extrato: Annotated[UploadFile | None, File()] = None,
    comprovante_de_credito: Annotated[UploadFile | None, File()] = None,
    dossie: Annotated[UploadFile | None, File()] = None,
    demonstrativo_evolucao_divida: Annotated[UploadFile | None, File()] = None,
    laudo_referenciado: Annotated[UploadFile | None, File()] = None,
    client: DocumentExtractionClient = Depends(get_document_extraction_client),
) -> PipelineAnalysisResponse:
    """Extract claims from the autos, then verify them with supplied subsidies in parallel."""
    resolved_case_id = case_id or f"case-{uuid4().hex}"
    incoming_files: dict[DocumentKind, UploadFile | None] = {
        DocumentKind.AUTOS_DO_PROCESSO: autos_do_processo,
        DocumentKind.CONTRATO: contrato,
        DocumentKind.EXTRATO: extrato,
        DocumentKind.COMPROVANTE_DE_CREDITO: comprovante_de_credito,
        DocumentKind.DOSSIE: dossie,
        DocumentKind.DEMONSTRATIVO_EVOLUCAO_DIVIDA: demonstrativo_evolucao_divida,
        DocumentKind.LAUDO_REFERENCIADO: laudo_referenciado,
    }
    if autos_do_processo is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Envie os autos_do_processo: eles definem as afirmações a verificar.",
        )
    try:
        claims_result = await client.extract_claims(
            case_id=resolved_case_id,
            upload=autos_do_processo,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except DocumentExtractionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    claims = claims_result.output.get("claims", [])
    targeted_uploads = [(kind, upload) for kind, upload in incoming_files.items()
                        if kind != DocumentKind.AUTOS_DO_PROCESSO and upload is not None]

    async def extract_one(kind: DocumentKind, upload: UploadFile) -> tuple[DocumentKind, str, dict]:
        result = await client.extract_targeted_evidence(
            case_id=resolved_case_id, kind=kind, upload=upload, claims=claims
        )
        return kind, upload.filename or f"{kind.value}.pdf", result.output

    try:
        document_results = await asyncio.gather(*(extract_one(kind, upload) for kind, upload in targeted_uploads))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except DocumentExtractionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    analysis = build_case_assessment(
        claims_result=claims_result.output,
        document_results=list(document_results),
        all_kinds=list(DocumentKind),
    )
    claim_catalogs, evidence_catalog = build_claim_flag_catalogs(analysis)
    warnings: list[dict[str, str]] = []
    candidates: list[dict] = []

    async def classify_claim(claim_id: str, payload: dict) -> tuple[str, dict]:
        result = await client.classify_claim_flag(
            claim=payload["claim"], evidence_catalog=payload["evidence"]
        )
        return claim_id, result.output

    classified = await asyncio.gather(
        *(classify_claim(claim_id, payload) for claim_id, payload in claim_catalogs.items()),
        return_exceptions=True,
    )
    for claim_id, outcome in zip(claim_catalogs, classified, strict=True):
        if isinstance(outcome, Exception):
            warnings.append({"claim_id": claim_id, "detail": str(outcome)})
            continue
        _, output = outcome
        candidates.extend({**candidate, "claim_id": claim_id} for candidate in output.get("flags", []))

    analysis.update(score_and_rank_flags(candidates, evidence_catalog))
    analysis["flag_classification"] = {
        "mode": "per_claim_parallel_inference",
        "claim_count": len(claim_catalogs),
        "warnings": warnings,
    }
    return PipelineAnalysisResponse(
        case_id=resolved_case_id,
        model=client.model,
        claims=claims,
        analysis=analysis,
    )
