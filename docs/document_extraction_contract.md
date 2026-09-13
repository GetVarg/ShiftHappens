# Pipeline orientada por afirmações v3

A pipeline não descreve exaustivamente cada PDF. Ela responde à pergunta: **quais afirmações
relevantes dos autos encontram suporte nos documentos apresentados?**

1. `POST /pipeline/analyze` recebe obrigatoriamente `autos_do_processo` e os subsídios que houver.
2. A primeira chamada extrai dos autos somente as afirmações verificáveis do autor (`C01`, `C02`...).
3. Cada subsídio recebe essa lista e procura somente evidência pertinente, além de seus poucos
   campos financeiros/autenticatórios obrigatórios.
4. Os subsídios são analisados em paralelo. A consolidação é local e determinística; não há uma
   chamada final ao modelo para resumir todos os PDFs.

## Entrada de PDF

Os PDFs são lidos localmente com PyMuPDF por página, usando `get_text("text", sort=True)`. A
OpenAI recebe somente esse texto paginado — não recebe o arquivo PDF, imagens ou OCR. Página com
menos de 50 caracteres extraíveis é sinalizada como limitação; se todas forem assim, a análise é
recusada em vez de produzir uma conclusão com base em imagem.

## Saída

```json
{
  "case_id": "...",
  "model": "...",
  "claims": [{"claim_id": "C01", "claim": "...", "field": "..."}],
  "analysis": {
    "subsidies": {"contrato": false, "extrato": false},
    "core_facts": {},
    "fact_sources": {},
    "claims_assessment": [],
    "internal_issues": [],
    "missing_evidence": []
  }
}
```

`claims_assessment.status` pode ser `corroborated`, `contradicted`, `contested`,
`partially_supported`, `not_corroborated` ou `inconclusive`. Um documento emitido pelo banco não
é confirmação independente: sua versão contrária gera `contested`, não `contradicted`, salvo
quando houver fonte externa verificável. Vídeo de liveness, logs, termo de consentimento e extrato
da conta destinatária citados mas não apresentados entram em `missing_evidence`.

Antes de uma evidência alterar a direção de uma claim, ela passa por verificações literal e
temporal. Um extrato que não cobre o período relevante não pode provar nem desmentir um fato
posterior. Itens excluídos por essa razão aparecem em `analysis.temporal_validation.excluded_evidence`.
Quando o contexto permite, eles podem ser enviados à avaliação da própria claim somente como
`temporal_consistency_context`, nunca como prova de pagamento posterior.

O uso de `json_schema` estrito na Responses API segue a
[documentação de Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

## Flags e ranking

Após a consolidação das claims, a pipeline executa uma classificação em paralelo para cada claim
que tenha evidência ou lacuna probatória. Cada chamada recebe somente a claim e seu catálogo neutro
de trechos e lacunas já extraídos — os PDFs não são enviados novamente — e produz no máximo uma
flag: `green`, se corroborar que a cobrança não foi indevida; `red`, se corroborar que ela foi
indevida; ou nenhuma flag se não houver elemento significativo.

O modelo classifica fonte, assimetria e impacto no quantum. O código valida os IDs de evidência,
busca `peso_juridico` e `fundamento_juridico` em uma tabela controlada por categoria e calcula
`independencia_fonte`, `assimetria`, `impacto_quantum` e `score_total`.

`analysis.flags` preserva todas as flags aceitas. `analysis.ranking` contém apenas IDs das flags
representantes: itens de autoria da contratação e comportamento pós-crédito são deduplicados dentro
do mesmo lado (`red` ou `green`), sem ocultar uma evidência contrária do outro lado. Candidatos com
ID de evidência inexistente são descartados e registrados em `rejected_flag_candidates`.
