import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from api.auth import require_internal_token
from contract.pipeline import run_pipeline
from contract.schemas import AIInputContract, AIOutputContract

logger = logging.getLogger("ai_service")

OPENAPI_TAGS = [
    {
        "name": "internal",
        "description": "Internal service-to-service endpoints. POST /v1/conversation/turn "
        "requires the Section 25 bearer token; /health is the readiness probe.",
    }
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast when the internal token is absent: the service must never
    # start without its Section 25 credential (no development bypass).
    if not os.environ.get("AI_SERVICE_INTERNAL_TOKEN"):
        raise RuntimeError("AI_SERVICE_INTERNAL_TOKEN is not set")
    yield


app = FastAPI(
    title="AI Talking Toy - AI Service (internal)",
    version="0.9.0",
    lifespan=lifespan,
    openapi_tags=OPENAPI_TAGS,
)


@app.exception_handler(Exception)
async def controlled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Controlled HTTP error: no stack traces, secrets, tokens, or paths
    # ever leave the service (Section 28 / Phase 9 failure behavior).
    logger.error("unhandled error on %s %s: %s", request.method, request.url.path, type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/v1/conversation/turn",
    response_model=AIOutputContract,
    dependencies=[Depends(require_internal_token)],
)
async def conversation_turn(payload: AIInputContract) -> AIOutputContract:
    # Thin API layer: validation is schema-driven, orchestration lives in
    # contract/pipeline, response construction in contract/assembler.
    return run_pipeline(payload)
