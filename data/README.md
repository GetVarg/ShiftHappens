# Dados

Coloque aqui os arquivos fornecidos pela organização do hackathon.

## Estrutura esperada

```
data/
├── sentencas.csv          # 60.000 sentenças judiciais
├── subsidios/             # documentos de subsídio por processo
│   └── <id_processo>/
│       ├── contrato.*
│       ├── extrato.*
│       ├── comprovante_credito.*
│       ├── dossie.*
│       ├── demonstrativo_divida.*
│       └── laudo_referenciado.*
└── processos_exemplo/     # 2 pastas de processos para simulação
    ├── processo_01/
    │   ├── autos/
    │   └── subsidios/
    └── processo_02/
        ├── autos/
        └── subsidios/
```

## Importante

- Os dados são fornecidos pela organização do hackathon e **não devem ser versionados** neste repositório.
- O `.gitignore` já está configurado para ignorar os arquivos de dados.
- Para fins de demonstração, você pode incluir dados sintéticos ou anonimizados.
