# Setup e Execução

> Preencha este arquivo com as instruções específicas da sua solução.

---

## Pré-requisitos

Liste aqui as dependências necessárias para rodar a solução:

- [ ] Python 3.11+
- [ ] Chave da OpenAI fornecida pelo hackathon

## Variáveis de Ambiente

Crie um arquivo `.env` na raiz do projeto com as variáveis necessárias:

```env
OPENAI_API_KEY=sua_chave_aqui
OPENAI_MODEL=gpt-4.1-mini
UPLOAD_DIR=data/uploads
```

> **Nunca commite o arquivo `.env` com credenciais reais.**  
> Um arquivo `.env.example` com as variáveis (sem valores) já está incluído neste repo.

## Instalação

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

> Use `python -m uvicorn` em vez de somente `uvicorn`. Isso garante que o servidor use o
> mesmo interpretador no qual as dependências foram instaladas.

Se você já tinha dependências instaladas no Miniconda, atualize-as uma vez antes de iniciar:

```powershell
python -m pip install --upgrade -r requirements.txt
```

## Execução

```bash
python -m uvicorn app.main:app --app-dir src --reload
```

A API ficara disponivel em `http://127.0.0.1:8000`.

### Entrada da pipeline

O front-end deve enviar `multipart/form-data` para:

```http
POST /pipeline/intake
```

Campos aceitos:

| Campo | Obrigatório | Descrição |
|---|---:|---|
| `case_id` | Não | Identificador do processo. Se ausente, a API gera um ID. |
| `autos_do_processo` | Não | Autos do processo (petição, procuração e demais peças). |
| `contrato` | Não | PDF do contrato. |
| `extrato` | Não | PDF do extrato. |
| `comprovante_de_credito` | Não | PDF do comprovante de crédito. |
| `dossie` | Não | PDF do dossiê de autenticação. |
| `demonstrativo_evolucao_divida` | Não | PDF do demonstrativo de evolução da dívida. |
| `laudo_referenciado` | Não | PDF do laudo referenciado. |

Exemplo:

```bash
curl -X POST http://127.0.0.1:8000/pipeline/intake ^
  -F "case_id=processo-demo-001" ^
  -F "autos_do_processo=@data/exemplos/autos.pdf" ^
  -F "contrato=@data/exemplos/contrato.pdf"
```

### Interface com OpenAI

Endpoint preparado para testes iniciais:

```http
POST /ai/chat
```

Body:

```json
{
  "prompt": "Resuma quais informacoes devem ser analisadas neste caso.",
  "system_prompt": "Opcional: instrucao de sistema para o modelo."
}
```

### Testar a extração de um caso

Com a API em execução, envie todos os PDFs disponíveis de **um** caso ao endpoint abaixo.
Ele chama a OpenAI uma vez por arquivo e devolve, no mesmo JSON, a extração individual de
cada documento. Cada PDF é carregado temporariamente como `user_data`, referenciado por
`file_id` na análise e removido logo após a resposta; esta rota não o salva em `data/uploads`.

Se a OpenAI recusar um arquivo, modelo ou schema, a rota devolve `502` com a mensagem da API
no campo `detail`; ela não deve mais responder apenas `500` sem diagnóstico. Para erros de
conexão, a mensagem informa se a falha ocorreu no upload ou na análise e o tamanho do PDF.

```http
POST /pipeline/analyze
```

Exemplo para o primeiro caso (PowerShell):

```powershell
curl.exe -X POST http://127.0.0.1:8000/pipeline/analyze `
  -F "case_id=caso-01" `
  -F "autos_do_processo=@C:\caminho\caso-01\autos.pdf" `
  -F "contrato=@C:\caminho\caso-01\contrato.pdf" `
  -F "extrato=@C:\caminho\caso-01\extrato.pdf" `
  -F "comprovante_de_credito=@C:\caminho\caso-01\comprovante.pdf" `
  -F "dossie=@C:\caminho\caso-01\dossie.pdf" `
  -F "demonstrativo_evolucao_divida=@C:\caminho\caso-01\demonstrativo.pdf" `
  -F "laudo_referenciado=@C:\caminho\caso-01\laudo.pdf"
```

Repita, trocando `case_id=caso-02` e os caminhos dos arquivos. Para uma visualização mais
confortável, abra `http://127.0.0.1:8000/docs`, escolha `POST /pipeline/analyze`, clique em
**Try it out**, anexe os arquivos e clique em **Execute**.

## Dados

Coloque os arquivos de dados fornecidos na pasta `data/`. Consulte [`data/README.md`](./data/README.md) para instruções detalhadas.

## Estrutura do Projeto

```
├── src/          # código-fonte
├── data/         # dados (não versionados — ver .gitignore)
├── docs/         # apresentação e documentação
├── .env.example  # variáveis de ambiente necessárias
├── SETUP.md      # este arquivo
└── README.md     # descrição do desafio
```
