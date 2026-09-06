"""GET /tools -- what the assistant can actually do right now (Phase 5).

Reports availability, not just existence. "web_search exists" and
"web_search can run" are different facts, and the UI needs the second one:
document search is useless until something is uploaded, and search degrades
to a keyless provider when SEARCH_API_KEY is absent. Surfacing the reason a
tool is unavailable is what stops "why didn't it look that up?" being a
mystery.
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id
from app.core.database import get_session
from app.models.schemas import ToolInfo
from app.tools.registry import ToolRegistry, get_tool_registry

router = APIRouter(tags=["tools"])


@router.get("/tools", response_model=list[ToolInfo])
async def list_tools(
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> list[ToolInfo]:
    return [
        ToolInfo(
            name=entry.tool.name,
            description=entry.tool.description,
            requires_permission=entry.tool.requires_permission,
            available=entry.usable,
            reason=entry.reason,
        )
        for entry in await registry.availability(session, user_id=user_id)
    ]
