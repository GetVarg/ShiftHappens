from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status

from app.services.openai_chat import OpenAIChatClient, get_openai_chat_client


router = APIRouter(prefix="/ai", tags=["ai"])


class ChatRequest(BaseModel):
    prompt: str = Field(min_length=1)
    system_prompt: str | None = None


class ChatResponse(BaseModel):
    model: str
    output_text: str


@router.post("/chat", response_model=ChatResponse)
async def chat_with_model(
    payload: ChatRequest,
    client: OpenAIChatClient = Depends(get_openai_chat_client),
) -> ChatResponse:
    try:
        result = await client.respond(
            prompt=payload.prompt,
            system_prompt=payload.system_prompt,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return ChatResponse(model=result.model, output_text=result.output_text)
