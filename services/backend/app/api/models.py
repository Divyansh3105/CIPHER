"""GET /models, POST /models/active, DELETE /models/active.

Phase 4's runtime brain swap. The interesting endpoint is POST
/models/active, which takes what the user *said* rather than a model id and
either resolves it exactly or refuses with the list of models that do exist
(app/llm/registry.py explains why there is no near-match fallback).

A 404 here is a real answer, not a failure: "I don't have that model, here
is what I have" is more useful than quietly loading something else.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.llm.registry import MODEL_REGISTRY, UnknownModelError, is_reset_phrase, resolve_model
from app.llm.router import LLMRouter, get_llm_router
from app.models.schemas import ActiveModel, ModelInfo, ModelsResponse, ModelSwapRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/models", tags=["models"])


def _active(llm_router: LLMRouter) -> ActiveModel:
    pinned = llm_router.pinned
    default_id = llm_router.default_model_id()
    if pinned is None:
        return ActiveModel(
            pinned=False,
            id=default_id,
            provider="gemini",
            display_name="Default routing",
            default_id=default_id,
        )
    return ActiveModel(
        pinned=True,
        id=pinned.id,
        provider=pinned.provider,
        display_name=pinned.display_name,
        default_id=default_id,
    )


@router.get("", response_model=ModelsResponse)
async def list_models(llm_router: LLMRouter = Depends(get_llm_router)) -> ModelsResponse:
    return ModelsResponse(
        active=_active(llm_router),
        available=[
            ModelInfo(
                id=spec.id,
                provider=spec.provider,
                display_name=spec.display_name,
                aliases=list(spec.aliases),
                note=spec.note,
            )
            for spec in MODEL_REGISTRY
        ],
    )


@router.post("/active", response_model=ActiveModel)
async def set_active_model(
    payload: ModelSwapRequest,
    llm_router: LLMRouter = Depends(get_llm_router),
) -> ActiveModel:
    if is_reset_phrase(payload.spoken):
        llm_router.unpin()
        return _active(llm_router)

    try:
        spec = resolve_model(payload.spoken)
    except UnknownModelError as exc:
        # Deliberately does NOT fall back to the closest match. The response
        # body is the refusal *plus* the real options, so the caller can
        # recover in one step.
        logger.info("Refused unknown model %r", payload.spoken)
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    llm_router.pin(spec)
    return _active(llm_router)


@router.delete("/active", response_model=ActiveModel)
async def clear_active_model(llm_router: LLMRouter = Depends(get_llm_router)) -> ActiveModel:
    llm_router.unpin()
    return _active(llm_router)
