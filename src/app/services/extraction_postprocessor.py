"""Deterministic validation, masking and arithmetic for extraction results."""

from __future__ import annotations

import copy
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


CPF_CNPJ_RE = re.compile(r"(?<!\d)(?:\d{3}\.?\d{3}\.?\d{3}-?\d{2}|\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})(?!\d)")
LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{8,}(?!\d)")
SCHEDULE_FIELD_RE = re.compile(r"^debt\.schedule\.(\d+)\.(opening_balance|interest|amortization|installment_amount|closing_balance)$")
REFERENCE_ALIASES = {"video_liveness": ("liveness", "vídeo", "video"), "device_logs": ("log", "device", "dispositivo"), "consent_term": ("consent", "termo"), "ted_receipt": ("ted", "transferência", "transferencia")}


def _mask_match(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return "*" * max(0, len(digits) - 4) + digits[-4:]


def mask_sensitive_text(value: str) -> str:
    """Mask common Brazilian identifiers deterministically, retaining only four last digits."""
    value = CPF_CNPJ_RE.sub(_mask_match, value)
    return LONG_NUMBER_RE.sub(_mask_match, value)


def _mask_value(field: str, value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if any(token in field for token in ("identifier", "account", "device_id", ".ip")):
        return mask_sensitive_text(value)
    return mask_sensitive_text(value)


def _evidence(document: str, page: int | None = None, excerpt: str = "") -> dict[str, Any]:
    return {"document": document, "page": page, "excerpt": mask_sensitive_text(excerpt)[:300], "coordinates": None}


def _observation(code: str, kind: str, description: str, fields: list[str], document: str, page: int | None = None, excerpt: str = "") -> dict[str, Any]:
    return {"code": code, "type": kind, "description": description, "related_fields": fields, "evidence": _evidence(document, page, excerpt)}


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value).replace(".", "."))
    except (InvalidOperation, ValueError):
        return None


def _fact(field: str, value: Decimal, document: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "field": field,
        "value": float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "epistemic_type": "resultado_calculado",
        "asserted_by": "sistema",
        "verification_status": "not_applicable",
        "value_status": "found",
        "extraction_quality": "high",
        "evidence": {**_evidence(document), **evidence},
    }


def _first_value(facts: list[dict[str, Any]], field: str) -> tuple[Decimal | None, dict[str, Any] | None]:
    for item in facts:
        if item.get("field") == field and item.get("value_status") == "found":
            value = _decimal(item.get("value"))
            if value is not None:
                return value, item
    return None, None


def _manifest_contains(value: str, manifest: list[dict[str, str]]) -> bool:
    target = value.lower()
    names = " ".join(str(item.get("filename", "")).lower() + " " + str(item.get("kind", "")).lower() for item in manifest)
    if target in names:
        return True
    return any(alias in target and any(word in names for word in words) for alias, words in REFERENCE_ALIASES.items())


def _validate_references(result: dict[str, Any], manifest: list[dict[str, str]]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for item in result["facts"]:
        if item.get("field") != "reference.artifact" or not isinstance(item.get("value"), str):
            continue
        if _manifest_contains(item["value"], manifest):
            continue
        evidence = item["evidence"]
        item["value_status"] = "referenced_but_not_provided"
        observations.append(_observation("REFERENCED_DOCUMENT_MISSING", "evidencia_mencionada_ausente", f"Artefato referenciado, mas não fornecido: {item['value']}.", ["reference.artifact"], evidence["document"], evidence["page"], evidence["excerpt"]))
    return observations


def _financial_checks(result: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    facts = result["facts"]
    document = result["document"]["filename"]
    additions: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    principal, principal_fact = _first_value(facts, "contract.amount")
    if principal is None:
        principal, principal_fact = _first_value(facts, "contract.financed_amount")
    rate, _ = _first_value(facts, "contract.interest_monthly")
    installments, _ = _first_value(facts, "contract.installments")
    payment, payment_fact = _first_value(facts, "contract.installment_amount")
    paid, paid_fact = _first_value(facts, "debt.paid_installments")
    reported, reported_fact = _first_value(facts, "debt.reported_balance")

    if principal and rate is not None and installments and payment:
        n = int(installments)
        if n > 0 and rate >= 0:
            expected_payment = principal / Decimal(n) if rate == 0 else principal * rate / (Decimal(1) - (Decimal(1) + rate) ** Decimal(-n))
            if abs(expected_payment - payment) > Decimal("0.02"):
                observations.append(_observation("PRICE_PAYMENT_MISMATCH", "inconsistencia_numerica", "A parcela informada não reconcilia com a fórmula Price usando principal, taxa mensal e prazo informados.", ["contract.amount", "contract.interest_monthly", "contract.installments", "contract.installment_amount"], document, payment_fact["evidence"]["page"], payment_fact["evidence"]["excerpt"]))
            if paid is not None and 0 <= int(paid) <= n:
                remaining = principal * (Decimal(1) + rate) ** int(paid) - expected_payment * (((Decimal(1) + rate) ** int(paid) - 1) / rate) if rate else principal - expected_payment * int(paid)
                evidence = paid_fact["evidence"] if paid_fact else _evidence(document)
                additions.append(_fact("debt.calculated_balance", max(remaining, Decimal(0)), document, evidence))
                if reported is not None and abs(reported - remaining) > Decimal("0.02"):
                    observations.append(_observation("REPORTED_BALANCE_MISMATCH", "inconsistencia_numerica", "O saldo reportado diverge do saldo Price calculado após a quantidade de parcelas pagas informada.", ["debt.reported_balance", "debt.paid_installments", "debt.calculated_balance"], document, reported_fact["evidence"]["page"], reported_fact["evidence"]["excerpt"]))

    rows: dict[str, dict[str, tuple[Decimal, dict[str, Any]]]] = {}
    for item in facts:
        match = SCHEDULE_FIELD_RE.match(str(item.get("field")))
        amount = _decimal(item.get("value"))
        if match and amount is not None and item.get("value_status") == "found":
            rows.setdefault(match.group(1), {})[match.group(2)] = (amount, item)
    previous_close: Decimal | None = None
    for number in sorted(rows, key=int):
        row = rows[number]
        if {"interest", "amortization", "installment_amount"} <= row.keys():
            if abs(row["interest"][0] + row["amortization"][0] - row["installment_amount"][0]) > Decimal("0.02"):
                item = row["installment_amount"][1]
                observations.append(_observation("SCHEDULE_PAYMENT_MISMATCH", "inconsistencia_numerica", f"Na parcela {number}, juros + amortização divergem da parcela.", [f"debt.schedule.{number}.interest", f"debt.schedule.{number}.amortization", f"debt.schedule.{number}.installment_amount"], document, item["evidence"]["page"], item["evidence"]["excerpt"]))
        if {"opening_balance", "amortization", "closing_balance"} <= row.keys():
            if abs(row["opening_balance"][0] - row["amortization"][0] - row["closing_balance"][0]) > Decimal("0.02"):
                item = row["closing_balance"][1]
                observations.append(_observation("SCHEDULE_BALANCE_MISMATCH", "inconsistencia_numerica", f"Na parcela {number}, saldo anterior menos amortização diverge do saldo final.", [f"debt.schedule.{number}.opening_balance", f"debt.schedule.{number}.amortization", f"debt.schedule.{number}.closing_balance"], document, item["evidence"]["page"], item["evidence"]["excerpt"]))
        if previous_close is not None and "opening_balance" in row and abs(previous_close - row["opening_balance"][0]) > Decimal("0.02"):
            item = row["opening_balance"][1]
            observations.append(_observation("SCHEDULE_SEQUENCE_MISMATCH", "inconsistencia_numerica", f"A abertura da parcela {number} não coincide com o fechamento da parcela anterior.", [f"debt.schedule.{number}.opening_balance"], document, item["evidence"]["page"], item["evidence"]["excerpt"]))
        if "closing_balance" in row:
            previous_close = row["closing_balance"][0]
    return additions, observations


def postprocess_extraction(raw: dict[str, Any], *, case_id: str, kind: str, filename: str, manifest: list[dict[str, str]]) -> dict[str, Any]:
    """Make a model extraction safe and deterministic before it reaches the orchestrator."""
    result = copy.deepcopy(raw)
    result.setdefault("document", {})
    result["document"]["identifier"] = result["document"].get("identifier") or f"{case_id}:{kind}:{filename}"
    result["document"]["filename"] = filename
    result["document"]["declared_type"] = kind
    result.setdefault("facts", [])
    result.setdefault("observations", [])
    result.setdefault("lawyer_summary", {"purpose": "", "key_points": [], "caveat": ""})
    clean_facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fact in result["facts"]:
        if not isinstance(fact, dict):
            continue
        evidence = fact.get("evidence") or {}
        fact["value"] = _mask_value(str(fact.get("field", "")), fact.get("value"))
        fact["evidence"] = _evidence(str(evidence.get("document") or filename), evidence.get("page") if isinstance(evidence.get("page"), int) and evidence.get("page") > 0 else None, str(evidence.get("excerpt") or ""))
        key = repr((fact.get("field"), fact.get("value"), fact["evidence"]["document"], fact["evidence"]["page"], fact["evidence"]["excerpt"]))
        if key not in seen:
            seen.add(key)
            clean_facts.append(fact)
    result["facts"] = clean_facts
    for observation in result["observations"]:
        if isinstance(observation, dict):
            evidence = observation.get("evidence") or {}
            observation["evidence"] = _evidence(str(evidence.get("document") or filename), evidence.get("page") if isinstance(evidence.get("page"), int) and evidence.get("page") > 0 else None, str(evidence.get("excerpt") or ""))
    result["observations"].extend(_validate_references(result, manifest))
    additions, checks = _financial_checks(result)
    result["facts"].extend(additions)
    result["observations"].extend(checks)
    return result
