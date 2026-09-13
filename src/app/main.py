from fastapi import FastAPI

from app.routes import ai, intake


app = FastAPI(
    title="ShiftHappens Agreement Policy API",
    description="Pipeline intake API for legal documents and AI-assisted case analysis.",
    version="0.1.0",
)


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(intake.router)
app.include_router(ai.router)
