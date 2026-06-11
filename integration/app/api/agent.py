"""
Direct agent endpoint for testing the conversation agent outside of WhatsApp.

POST /api/agent/message — send a message and get the agent's response.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.router import AgentRouter
from app.db.session import get_session

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])


class MessageRequest(BaseModel):
    conversation_id: int
    waid: str
    message: str


@router.post("/message")
async def send_message(
    body: MessageRequest,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """
    Process a message through the agent and return immediately.
    Used for direct testing without WhatsApp.
    """
    agent_router = AgentRouter()
    try:
        await agent_router.handle(
            conversation_id=body.conversation_id,
            waid=body.waid,
            user_message=body.message,
            session=session,
        )
        return JSONResponse({"ok": True})
    except Exception as exc:
        logger.error("agent_endpoint_error", error=str(exc))
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
