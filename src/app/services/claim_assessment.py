"""Build the compact, claim-centred result without a second model call."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.documents import DocumentKind
from app.services.extraction_postprocessor import mask_sensitive_text


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _safe_evidence(item: dict[str, Any]) -> dict[str, Any]:
    evidence = item.get("evidence") or {}
    return {"document": evidence.get("document"), "page": evidence.get("page"), "excerpt": mask_sensitive_text(str(evidence.get("excerpt") or ""))[:240]}


def _debt_issues(core_facts: dict[str, Any], sources: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    principal = _decimal(core_facts.get("contract.amount") or core_facts.get("contract.financed_amount"))
    rate = _decimal(core_facts.get("contract.interest_monthly"))
    count = _decimal(core_facts.get("contract.installments"))
    payment = _decimal(core_facts.get("contract.installment_amount"))
    paid = _decimal(core_facts.get("debt.paid_installments"))
    reported = _decimal(core_facts.get("debt.reported_balance"))
    issues: list[dict[str, Any]] = []
    if not all(value is not None for value in (principal, rate, count, payment)) or count <= 0 or rate < 0:
        return issues
    expected = principal / count if rate == 0 else principal * rate / (Decimal(1) - (Decimal(1) + rate) ** -int(count))
    if abs(expected - payment) > Decimal("0.02"):
        issues.append({"code": "PRICE_PAYMENT_MISMATCH", "severity": "high", "description": "Valor, juros, prazo e parcela informados não amortizam integralmente a dívida pela fórmula Price.", "evidence": sources.get("contract.installment_amount")})
    if paid is not None and reported is not None and 0 <= paid <= count:
        remaining = principal * (Decimal(1) + rate) ** int(paid) - expected * (((Decimal(1) + rate) ** int(paid) - 1) / rate) if rate else principal - expected * paid
        if abs(reported - remaining) > Decimal("0.02"):
            issues.append({"code": "DEBT_BALANCE_MISMATCH", "severity": "high", "description": f"O saldo reportado não corresponde ao saldo Price após as {int(paid)} parcelas pagas informadas.", "evidence": sources.get("debt.reported_balance")})
    return issues


def _verified_relation(item: dict[str, Any]) -> str | None:
    """Gate a relation by literal and temporal checks before it affects a claim."""
    relation = item.get("relation")
    if relation not in {"supports_claim", "contradicts_claim", "partial"}:
        return None
    if item.get("temporal_relation") in {"outside_relevant_period", "partial_or_unclear"}:
        return None
    literal = item.get("literal_direction")
    # Preserve compatibility with extractions made before this validation existed.
    if literal is None:
        return relation
    if relation == "partial":
        return relation if literal == "supports_claim" else None
    return relation if literal == relation else None


def build_case_assessment(*, claims_result: dict[str, Any], document_results: list[tuple[DocumentKind, str, dict[str, Any]]], all_kinds: list[DocumentKind]) -> dict[str, Any]:
    """Merge targeted evidence into statuses; no claim is decided as legally true or false."""
    subsidies = {kind.value: any(doc_kind == kind for doc_kind, _, _ in document_results) for kind in all_kinds if kind != DocumentKind.AUTOS_DO_PROCESSO}
    core_facts: dict[str, Any] = {}
    fact_sources: dict[str, dict[str, Any]] = {}
    evidence_by_claim: dict[str, list[dict[str, Any]]] = {}
    issues: list[dict[str, Any]] = []
    missing: list[str] = list(claims_result.get("missing_evidence", []))
    for item in claims_result.get("case_facts", []):
        if isinstance(item, dict) and item.get("field"):
            core_facts[str(item["field"])] = item.get("value")
            fact_sources[str(item["field"])] = _safe_evidence(item)
    for kind, filename, output in document_results:
        for item in output.get("core_facts", []):
            if not isinstance(item, dict) or not item.get("field"):
                continue
            field = str(item["field"])
            core_facts.setdefault(field, item.get("value"))
            fact_sources.setdefault(field, _safe_evidence(item))
        for item in output.get("claim_evidence", []):
            if isinstance(item, dict) and item.get("claim_id"):
                item = {**item, "document_kind": kind.value, "filename": filename, "evidence": _safe_evidence(item)}
                evidence_by_claim.setdefault(str(item["claim_id"]), []).append(item)
                if item.get("availability") in {"referenced_but_not_provided", "declared_but_original_unavailable"}:
                    missing.append(item.get("assessment", "Artefato probatório principal indisponível."))
        for issue in output.get("internal_issues", []):
            if isinstance(issue, dict):
                issues.append({"code": issue.get("code", "INTERNAL_ISSUE"), "severity": "medium", "description": issue.get("description"), "evidence": _safe_evidence(issue)})
        missing.extend(output.get("missing_evidence", []))
    issues.extend(_debt_issues(core_facts, fact_sources))

    assessments: list[dict[str, Any]] = []
    temporal_exclusions: list[dict[str, Any]] = []
    for claim in claims_result.get("claims", []):
        if not isinstance(claim, dict):
            continue
        evidence = evidence_by_claim.get(str(claim.get("claim_id")), [])
        verified = [(item, _verified_relation(item)) for item in evidence]
        against = [item for item, relation in verified if relation == "contradicts_claim"]
        supports = [item for item, relation in verified if relation == "supports_claim"]
        partial = [item for item, relation in verified if relation == "partial"]
        excluded_by_time = [
            item for item in evidence
            if item.get("relation") in {"supports_claim", "contradicts_claim", "partial"}
            and item.get("temporal_relation") in {"outside_relevant_period", "partial_or_unclear"}
        ]
        limitations: list[dict[str, Any]] = []
        for item in excluded_by_time:
            limitation = {
                "document": item["evidence"].get("document"),
                "page": item["evidence"].get("page"),
                "excerpt": item["evidence"].get("excerpt"),
                "relevancia_para_claim": "fora_do_periodo",
                "reason": "O trecho não cobre com segurança o período relevante da alegação.",
            }
            limitations.append(limitation)
            temporal_exclusions.append({
                "claim_id": claim.get("claim_id"),
                "temporal_relation": item.get("temporal_relation"),
                **limitation,
                "flag_context_eligible": item.get("temporal_relation") == "outside_relevant_period",
            })
        missing_primary = any(item.get("availability") != "found" for item in evidence)
        if not against and not supports and not partial and excluded_by_time:
            status, reason = "inconclusive", "O documento não cobre com segurança o período relevante; sua ausência de lançamento não prova a alegação nem o contrário."
        elif any(item.get("support_strength") == "independent" for item in against):
            status, reason = "contradicted", "Há evidência independente incompatível com a afirmação."
        elif against and missing_primary:
            status, reason = "partially_supported", "Há indício em sentido contrário, mas o artefato primário ou original não foi apresentado."
        elif against:
            status, reason = "contested", "Há versão documental contrária, sem confirmação independente suficiente."
        elif any(item.get("support_strength") == "independent" for item in supports):
            status, reason = "corroborated", "Há evidência independente compatível com a afirmação."
        elif supports or partial:
            status, reason = "partially_supported", "Há indício documental compatível, mas ele não é suficiente ou independente."
        elif missing_primary or missing:
            status, reason = "inconclusive", "A documentação apresentada não contém o artefato necessário para verificar a afirmação."
        else:
            status, reason = "not_corroborated", "Nenhum documento apresentado oferece suporte específico à afirmação."
        assessments.append({"claim_id": claim.get("claim_id"), "claim": claim.get("claim"), "field": claim.get("field"), "asserted_by": claim.get("asserted_by"), "status": status, "reason": reason, "evidence_for": [item["evidence"] for item in supports], "evidence_against": [item["evidence"] for item in against], "evidence_with_limitations": limitations, "missing_evidence": [item["assessment"] for item in evidence if item.get("availability") != "found"]})
    return {"subsidies": subsidies, "core_facts": core_facts, "fact_sources": fact_sources, "claims_assessment": assessments, "internal_issues": issues, "missing_evidence": list(dict.fromkeys(str(item) for item in missing if item)), "temporal_validation": {"excluded_evidence": temporal_exclusions}}
