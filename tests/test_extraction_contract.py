import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz

from app.models.documents import DocumentKind
from app.services.claim_assessment import build_case_assessment
from app.services.extraction_postprocessor import mask_sensitive_text, postprocess_extraction
from app.services.pdf_text import extract_pdf_text
from app.services.document_extraction import _parse_structured_output
from app.services.flag_scoring import build_claim_flag_catalogs, score_and_rank_flags
from app.prompts.flag_classification import get_flag_candidate_schema


def evidence():
    return {"document": "divida.pdf", "page": 1, "excerpt": "teste", "coordinates": None}


def fact(field, value):
    return {"field": field, "value": value, "epistemic_type": "fato_documental", "asserted_by": "emissor_do_documento", "verification_status": "documented", "value_status": "found", "extraction_quality": "high", "evidence": evidence()}


class ExtractionPostprocessorTests(unittest.TestCase):
    def test_masks_identifiers_and_calculates_remaining_balance(self):
        raw = {"document": {"identifier": "x", "filename": "x", "declared_type": "demonstrativo_evolucao_divida", "detected_type": None, "subsidy_category": None, "issuer": None, "issuer_role": None, "pages": 1}, "facts": [fact("party.autor.identifier", "123.456.789-01"), fact("contract.amount", 1000), fact("contract.interest_monthly", 0.01), fact("contract.installments", 12), fact("contract.installment_amount", 88.85), fact("debt.paid_installments", 8), fact("debt.reported_balance", 300)], "observations": [], "lawyer_summary": {"purpose": "", "key_points": [], "caveat": ""}}
        result = postprocess_extraction(raw, case_id="c1", kind="demonstrativo_evolucao_divida", filename="divida.pdf", manifest=[])
        self.assertEqual(result["facts"][0]["value"], "*******8901")
        self.assertTrue(any(item["field"] == "debt.calculated_balance" for item in result["facts"]))
        self.assertTrue(any(item["code"] == "REPORTED_BALANCE_MISMATCH" for item in result["observations"]))

    def test_marks_referenced_artifact_missing(self):
        raw = {"document": {"identifier": "x", "filename": "x", "declared_type": "dossie", "detected_type": None, "subsidy_category": None, "issuer": None, "issuer_role": None, "pages": 1}, "facts": [fact("reference.artifact", "video_liveness")], "observations": [], "lawyer_summary": {"purpose": "", "key_points": [], "caveat": ""}}
        result = postprocess_extraction(raw, case_id="c1", kind="dossie", filename="dossie.pdf", manifest=[])
        self.assertEqual(result["facts"][0]["value_status"], "referenced_but_not_provided")
        self.assertTrue(any(item["code"] == "REFERENCED_DOCUMENT_MISSING" for item in result["observations"]))

    def test_bank_document_without_independent_source_is_contested(self):
        claims = {"claims": [{"claim_id": "C02", "claim": "O autor não possui conta na Caixa", "field": "credit.destination_account.ownership_by_customer"}], "case_facts": [], "missing_evidence": []}
        output = {"core_facts": [], "claim_evidence": [{"claim_id": "C02", "relation": "contradicts_claim", "support_strength": "documentary", "availability": "found", "assessment": "O banco declara titularidade.", "evidence": evidence()}], "internal_issues": [], "missing_evidence": []}
        result = build_case_assessment(claims_result=claims, document_results=[(DocumentKind.COMPROVANTE_DE_CREDITO, "credito.pdf", output)], all_kinds=list(DocumentKind))
        self.assertEqual(result["claims_assessment"][0]["status"], "contested")

    def test_plain_text_masking(self):
        self.assertEqual(mask_sensitive_text("CPF 123.456.789-01"), "CPF *******8901")

    def test_text_only_pdf_preserves_page_boundaries_and_flags_empty_page(self):
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "Texto suficiente para teste de extração local por página. " * 3)
        pdf.new_page()
        result = extract_pdf_text(pdf.tobytes())
        self.assertEqual(result.page_count, 2)
        self.assertEqual(result.unreadable_pages, [2])
        self.assertIn('<page number="1" text_status="available">', result.text)
        self.assertIn('<page number="2" text_status="unreadable">', result.text)

    def test_json_parser_accepts_fenced_json_and_rejects_incomplete_response(self):
        class Response:
            status = "completed"
            output_text = "```json\n{\"claims\": []}\n```"
        self.assertEqual(_parse_structured_output(Response()), {"claims": []})
        class IncompleteResponse:
            status = "incomplete"
            error = None
            incomplete_details = "max_output_tokens"
        with self.assertRaisesRegex(Exception, "não concluiu"):
            _parse_structured_output(IncompleteResponse())

    def test_scores_all_axes_and_deduplicates_only_within_same_side(self):
        catalog = {
            "E001": {"document": "dossie.pdf", "page": 1, "excerpt": "Assinatura incompatível."},
            "E002": {"document": "laudo.pdf", "page": 2, "excerpt": "Liveness não localizado."},
            "E003": {"document": "pericia.pdf", "page": 1, "excerpt": "Compatibilidade de 91%."},
        }
        candidates = [
            {"tipo": "red", "categoria": "autenticidade_assinatura", "descricao": "Assinatura impugnada.", "fundamento_juridico": "Tema 1.061/STJ", "vinculo_juridico": "direto", "fonte_tipo": "documento_produzido_pelo_banco", "assimetria_classificacao": "parcial", "impacto_quantum_classificacao": "direto", "evidence_ids": ["E001"]},
            {"tipo": "red", "categoria": "biometria_liveness", "descricao": "Liveness ausente.", "fundamento_juridico": "Tema 1.061/STJ", "vinculo_juridico": "direto", "fonte_tipo": "inferencia_por_ausencia", "assimetria_classificacao": "zero", "impacto_quantum_classificacao": "direto", "evidence_ids": ["E002"]},
            {"tipo": "green", "categoria": "autenticidade_assinatura", "descricao": "Perícia favorável.", "fundamento_juridico": None, "vinculo_juridico": "indireto", "fonte_tipo": "prova_tecnica_independente", "assimetria_classificacao": "zero", "impacto_quantum_classificacao": "indireto", "evidence_ids": ["E003"]},
        ]
        result = score_and_rank_flags(candidates, catalog)
        self.assertEqual(result["flags"][0]["score_total"], 11)
        self.assertEqual(result["flags"][0]["peso_juridico"], 3)
        self.assertEqual(result["flags"][0]["independencia_fonte"], 2)
        self.assertEqual(result["flags"][0]["assimetria"], 1)
        self.assertEqual(result["flags"][0]["impacto_quantum"], 2)
        self.assertEqual(result["ranking"]["red_flag_ids"], ["R01"])
        self.assertEqual(result["ranking"]["green_flag_ids"], ["G01"])
        self.assertFalse(result["flags"][1]["representante_do_grupo"])

    def test_excludes_evidence_outside_the_claim_period(self):
        claims = {"claims": [{"claim_id": "C02", "claim": "Descontos foram realizados desde junho de 2022", "field": "descontos"}], "case_facts": [], "missing_evidence": []}
        output = {"core_facts": [], "claim_evidence": [{"claim_id": "C02", "relation": "contradicts_claim", "literal_direction": "contradicts_claim", "temporal_relation": "outside_relevant_period", "support_strength": "documentary", "availability": "found", "assessment": "Extrato cobre apenas maio de 2022.", "evidence": {"document": "extrato.pdf", "page": 1, "excerpt": "Não há débitos no período 01/05/2022 a 31/05/2022."}}], "internal_issues": [], "missing_evidence": []}
        result = build_case_assessment(claims_result=claims, document_results=[(DocumentKind.EXTRATO, "extrato.pdf", output)], all_kinds=list(DocumentKind))
        assessment = result["claims_assessment"][0]
        self.assertEqual(assessment["status"], "inconclusive")
        self.assertEqual(assessment["evidence_against"], [])
        self.assertEqual(assessment["evidence_with_limitations"][0]["relevancia_para_claim"], "fora_do_periodo")
        self.assertEqual(len(result["temporal_validation"]["excluded_evidence"]), 1)
        catalogs, _ = build_claim_flag_catalogs(result)
        self.assertTrue(any(item["context_type"] == "temporal_consistency_context" for item in catalogs["C02"]["evidence"]))

    def test_flag_schema_allows_case_wide_categories(self):
        schema = get_flag_candidate_schema()
        categories = schema["properties"]["flags"]["items"]["properties"]["categoria"]["enum"]
        self.assertIn("autenticidade_assinatura", categories)
        self.assertIn("titularidade_conta_destino", categories)

    def test_legal_weight_is_controlled_by_category_not_model_output(self):
        catalog = {
            "E001": {
                "document": "extrato.pdf",
                "page": 1,
                "excerpt": "TED, PIX e saque posteriores ao crédito.",
            }
        }
        candidate = {
            "tipo": "green",
            "categoria": "autenticidade_assinatura",
            "descricao": "Autenticidade documentada.",
            "fundamento_juridico": "Regra inventada pelo modelo",
            "vinculo_juridico": "nao_mapeia",
            "fonte_tipo": "documento_produzido_pelo_banco",
            "assimetria_classificacao": "parcial",
            "impacto_quantum_classificacao": "indireto",
            "evidence_ids": ["E001"],
        }
        result = score_and_rank_flags([candidate], catalog)
        self.assertEqual(result["flags"][0]["vinculo_juridico"], "direto")
        self.assertEqual(result["flags"][0]["fundamento_juridico"], "Tema 1.061/STJ")


if __name__ == "__main__":
    unittest.main()
