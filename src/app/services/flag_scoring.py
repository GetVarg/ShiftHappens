"""Validate, score and deduplicate AI-classified legal flag candidates."""

from __future__ import annotations

from typing import Any

PESO_JURIDICO = {"direto": 3, "indireto": 2, "nao_mapeia": 1}
INDEPENDENCIA_FONTE = {
    "prova_tecnica_independente": 3,
    "documento_produzido_pelo_banco": 2,
    "alegacao_parte_interessada": 1,
    "inferencia_por_ausencia": 0,
}
ASSIMETRIA = {"total": 2, "parcial": 1, "zero": 0}
IMPACTO_QUANTUM = {"direto": 2, "indireto": 1, "zero": 0}

GRUPO_FATO_BASE = {
    "autenticidade_assinatura": "autoria_contratacao",
    "biometria_liveness": "autoria_contratacao",
    "documentos_pessoais": "autoria_contratacao",
    "historico_pagamento": "comportamento_pos_credito",
    "uso_dos_recursos": "comportamento_pos_credito",
}

REFERENCIA_JURIDICA_POR_CATEGORIA = {
    "autenticidade_assinatura": ("direto", "Tema 1.061/STJ"),
    "admissao_falha_interna": ("indireto", "Súmula 479/STJ"),
    "vulnerabilidade_consumidor": ("indireto", "CDC, art. 39"),
}


def build_claim_flag_catalogs(analysis: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Create one small, neutral evidence catalogue for each claim assessment."""
    catalogs: dict[str, dict[str, Any]] = {}
    evidence_catalog: dict[str, dict[str, Any]] = {}
    sequence = 0

    def add(
        item: dict[str, Any],
        claim_id: str,
        claim_catalog: list[dict[str, Any]],
        *,
        context_type: str = "claim_evidence",
    ) -> None:
        nonlocal sequence
        if len(claim_catalog) >= 6:
            return
        sequence += 1
        evidence_id = f"E{sequence:03d}"
        evidence = {
            "evidence_id": evidence_id,
            "claim_id": claim_id,
            "context_type": context_type,
            "document": item.get("document"),
            "page": item.get("page"),
            "excerpt": str(item.get("excerpt") or "")[:200],
        }
        evidence_catalog[evidence_id] = evidence
        claim_catalog.append(evidence)

    temporal_by_claim: dict[str, list[dict[str, Any]]] = {}
    for item in analysis.get("temporal_validation", {}).get("excluded_evidence", []):
        if isinstance(item, dict) and item.get("flag_context_eligible"):
            temporal_by_claim.setdefault(str(item.get("claim_id") or ""), []).append(item)

    for assessment in analysis.get("claims_assessment", []):
        if not isinstance(assessment, dict):
            continue
        claim_id = str(assessment.get("claim_id") or "")
        claim_catalog: list[dict[str, Any]] = []
        for evidence_key in ("evidence_for", "evidence_against", "evidence_with_limitations"):
            for item in assessment.get(evidence_key, []):
                if isinstance(item, dict):
                    add(item, claim_id, claim_catalog, context_type="temporal_consistency_context" if evidence_key == "evidence_with_limitations" else "claim_evidence")
        for missing in assessment.get("missing_evidence", []):
            add(
                {"document": "missing_evidence", "page": None, "excerpt": str(missing)},
                claim_id,
                claim_catalog,
                context_type="explicit_proof_gap",
            )
        for item in temporal_by_claim.get(claim_id, []):
            add(item, claim_id, claim_catalog, context_type="temporal_consistency_context")
        if claim_catalog:
            catalogs[claim_id] = {
                "claim": {
                    "claim_id": claim_id,
                    "claim": assessment.get("claim"),
                    "field": assessment.get("field"),
                    "asserted_by": assessment.get("asserted_by"),
                    "status": assessment.get("status"),
                },
                "evidence": claim_catalog,
            }
    return catalogs, evidence_catalog


def score_and_rank_flags(
    candidates: list[dict[str, Any]], evidence_catalog: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Use only existing evidence and deterministic mappings for the ranking."""
    flags: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    counters = {"red": 0, "green": 0}

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        tipo = candidate.get("tipo")
        categoria = candidate.get("categoria")
        evidence_ids = candidate.get("evidence_ids")
        if (
            tipo not in counters
            or candidate.get("fonte_tipo") not in INDEPENDENCIA_FONTE
            or candidate.get("assimetria_classificacao") not in ASSIMETRIA
            or candidate.get("impacto_quantum_classificacao") not in IMPACTO_QUANTUM
            or not isinstance(evidence_ids, list)
            or not evidence_ids
            or any(item not in evidence_catalog for item in evidence_ids)
        ):
            rejected.append({"categoria": categoria, "reason": "Classificação inválida ou evidence_id não encontrado no catálogo."})
            continue

        counters[tipo] += 1
        vinculo_juridico, fundamento_juridico = REFERENCIA_JURIDICA_POR_CATEGORIA.get(
            categoria, ("nao_mapeia", None)
        )
        peso_juridico = PESO_JURIDICO[vinculo_juridico]
        independencia = INDEPENDENCIA_FONTE[candidate["fonte_tipo"]]
        assimetria = ASSIMETRIA[candidate["assimetria_classificacao"]]
        impacto = IMPACTO_QUANTUM[candidate["impacto_quantum_classificacao"]]
        grupo = GRUPO_FATO_BASE.get(categoria, categoria)
        flags.append({
            "flag_id": f"{'R' if tipo == 'red' else 'G'}{counters[tipo]:02d}",
            "claim_id": candidate.get("claim_id"),
            "tipo": tipo,
            "categoria": categoria,
            "descricao": str(candidate.get("descricao") or "")[:180],
            "fundamento_juridico": fundamento_juridico,
            "vinculo_juridico": vinculo_juridico,
            "fonte_tipo": candidate["fonte_tipo"],
            "assimetria_classificacao": candidate["assimetria_classificacao"],
            "impacto_quantum_classificacao": candidate["impacto_quantum_classificacao"],
            "peso_juridico": peso_juridico,
            "independencia_fonte": independencia,
            "assimetria": assimetria,
            "impacto_quantum": impacto,
            "score_total": 2 * peso_juridico + independencia + assimetria + impacto,
            "grupo_fato_base": grupo,
            "representante_do_grupo": False,
            "evidence": [evidence_catalog[item] for item in evidence_ids],
        })

    # Red and green evidence about the same fact remain visible in their own rankings.
    representatives: set[str] = set()
    for tipo in ("red", "green"):
        groups: dict[str, list[dict[str, Any]]] = {}
        for flag in flags:
            if flag["tipo"] == tipo:
                groups.setdefault(flag["grupo_fato_base"], []).append(flag)
        for group_flags in groups.values():
            selected = sorted(group_flags, key=lambda item: (-item["score_total"], item["flag_id"]))[0]
            representatives.add(selected["flag_id"])

    for flag in flags:
        flag["representante_do_grupo"] = flag["flag_id"] in representatives

    def ranking(tipo: str) -> list[str]:
        return [
            item["flag_id"]
            for item in sorted(
                (flag for flag in flags if flag["tipo"] == tipo and flag["representante_do_grupo"]),
                key=lambda item: (-item["score_total"], item["flag_id"]),
            )
        ]

    return {
        "flags": flags,
        "ranking": {"red_flag_ids": ranking("red"), "green_flag_ids": ranking("green")},
        "rejected_flag_candidates": rejected,
    }
