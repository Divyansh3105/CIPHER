"""GET /personas -- lets the frontend switcher read persona labels from the
backend instead of hardcoding a second copy of them (see docs/architecture.md,
Section 3: adding a persona should require no architecture change).
"""
from fastapi import APIRouter

from app.models.schemas import PersonaInfo
from app.personas import PERSONAS

router = APIRouter(tags=["personas"])


@router.get("/personas", response_model=list[PersonaInfo])
async def list_personas() -> list[PersonaInfo]:
    return [
        PersonaInfo(id=config.id, display_name=config.display_name, tagline=config.tagline)
        for config in PERSONAS.values()
    ]
