# Código-fonte da solução

A solução começa com uma API em FastAPI que recebe os documentos enviados pelo front-end e deixa pronta a camada de integração com a OpenAI.

## Organização atual

```
src/
└── app/
    ├── main.py                  # aplicação FastAPI
    ├── routes/
    │   ├── intake.py            # entrada da pipeline documental
    │   └── ai.py                # endpoint simples para OpenAI
    ├── models/
    │   └── documents.py         # tipos documentais aceitos
    └── services/
        ├── document_storage.py  # validação e armazenamento de PDFs
        └── openai_chat.py       # interface isolada com a OpenAI
```

Documentos aceitos na entrada da pipeline:

- `autos_do_processo`
- `contrato`
- `extrato`
- `comprovante_de_credito`
- `dossie`
- `demonstrativo_evolucao_divida`
- `laudo_referenciado`

O contrato e o prompt versionado para a extração estruturada estão em
[`docs/document_extraction_contract.md`](../docs/document_extraction_contract.md).
