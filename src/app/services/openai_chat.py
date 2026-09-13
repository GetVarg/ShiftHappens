from dataclasses import dataclass

from openai import AsyncOpenAI

from app.config import get_settings
from app.services.document_extraction import DocumentExtractionClient


DEFAULT_SYSTEM_PROMPT = (
    "Voce e um assistente juridico para triagem inicial de processos civeis "
    "massificados sobre nao reconhecimento de contratacao de emprestimo. "
    "Seja objetivo, explicite incertezas e nao invente fatos ausentes."
)


@dataclass(frozen=True)
class ChatResult:
    model: str
    output_text: str


class OpenAIChatClient:
    def __init__(self, api_key: str | None, model: str) -> None:
        self._api_key = api_key
        self._model = model
        self._client = AsyncOpenAI(api_key=api_key) if api_key else None

    async def respond(self, prompt: str, system_prompt: str | None = None) -> ChatResult:
        if self._client is None:
            raise RuntimeError("OPENAI_API_KEY nao configurada.")

        response = await self._client.responses.create(
            model=self._model,
            instructions=system_prompt or DEFAULT_SYSTEM_PROMPT,
            input=prompt,
        )

        return ChatResult(model=self._model, output_text=response.output_text)


def get_openai_chat_client() -> OpenAIChatClient:
    settings = get_settings()
    return OpenAIChatClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
    )


def get_document_extraction_client() -> DocumentExtractionClient:
    settings = get_settings()
    return DocumentExtractionClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
    )
