"""
Phase 5: Conversations REST API + SSE stream.

Endpoints (prefix /api/conversations):
  GET    ""                          - list conversations
  GET    "/events"                   - SSE stream of new messages
  GET    "/{conversation_id}"        - single conversation
  GET    "/{conversation_id}/messages" - message thread
  POST   "/{conversation_id}/takeover" - set status=human
  POST   "/{conversation_id}/release"  - set status=active
  POST   "/{conversation_id}/reply"    - send outbound message (human mode only)

IMPORTANT: /events is defined BEFORE /{conversation_id} to avoid routing conflict.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, nullslast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, Message
from app.db.session import get_session, get_session_factory

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _conv_dict(conv: Conversation, last_message: str | None) -> dict:
    return {
        "id": conv.id,
        "channel": conv.channel,
        "waid": conv.waid,
        "display_name": conv.display_name,
        "phone": conv.phone,
        "erp_customer_name": conv.erp_customer_name,
        "status": conv.status,
        "last_message": last_message,
        "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
    }


def _msg_dict(msg: Message) -> dict:
    return {
        "id": msg.id,
        "message_id": msg.message_id,
        "direction": msg.direction,
        "message_type": msg.message_type,
        "body": msg.body,
        "media_id": msg.media_id,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }


# ── Request models ─────────────────────────────────────────────────────────────

class ReplyBody(BaseModel):
    message: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
async def list_conversations(
    status: str = "all",
    limit: int = 20,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    """List conversations with optional status filter and pagination."""
    # Subquery: max message id per conversation (portable, no DISTINCT ON)
    max_id_subq = (
        select(Message.conversation_id, func.max(Message.id).label("max_id"))
        .group_by(Message.conversation_id)
        .subquery("last_msg_ids")
    )
    last_msg_subq = (
        select(Message.conversation_id, Message.body)
        .join(max_id_subq, Message.id == max_id_subq.c.max_id)
        .subquery("last_msg")
    )

    # Build base query
    stmt = select(Conversation, last_msg_subq.c.body).outerjoin(
        last_msg_subq, last_msg_subq.c.conversation_id == Conversation.id
    )

    if status != "all":
        stmt = stmt.where(Conversation.status == status)

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await session.execute(count_stmt)).scalar_one()

    # Paginate and order
    stmt = stmt.order_by(nullslast(Conversation.last_message_at.desc())).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).all()

    conversations = [_conv_dict(conv, body) for conv, body in rows]

    return {
        "conversations": conversations,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# NOTE: /events MUST be defined before /{conversation_id} to avoid route conflict
@router.get("/events")
async def sse_events():
    """
    Server-Sent Events stream.

    Polls DB every 2 seconds for new messages and emits them as SSE events.
    Does NOT use Depends(get_session) — creates its own sessions via get_session_factory().
    """

    async def generator():
        last_seen_id: int = 0
        yield ": connected\n\n"

        while True:
            try:
                factory = get_session_factory()
                async with factory() as session:
                    stmt = (
                        select(Message, Conversation)
                        .join(Conversation, Conversation.id == Message.conversation_id)
                        .where(Message.id > last_seen_id)
                        .order_by(Message.id.asc())
                    )
                    rows = (await session.execute(stmt)).all()

                if rows:
                    for msg, conv in rows:
                        last_seen_id = max(last_seen_id, msg.id)
                        payload = {
                            "type": "message",
                            "conversation_id": msg.conversation_id,
                            "message_id": msg.id,
                            "direction": msg.direction,
                            "body": msg.body,
                            "waid": conv.waid,
                            "display_name": conv.display_name,
                            "created_at": msg.created_at.isoformat() if msg.created_at else None,
                        }
                        yield f"data: {json.dumps(payload)}\n\n"
                else:
                    yield ": heartbeat\n\n"

                await asyncio.sleep(2)

            except Exception as exc:
                logger.warning("sse_error", error=str(exc))
                yield f": error {exc}\n\n"
                await asyncio.sleep(2)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Retrieve a single conversation by ID."""
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Fetch last message body
    last_msg_stmt = (
        select(Message.body)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(1)
    )
    last_body = (await session.execute(last_msg_stmt)).scalar_one_or_none()

    return _conv_dict(conv, last_body)


@router.get("/{conversation_id}/messages")
async def get_messages(
    conversation_id: int,
    limit: int = 50,
    before_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
):
    """Retrieve the message thread for a conversation."""
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    stmt = select(Message).where(Message.conversation_id == conversation_id)
    if before_id is not None:
        stmt = stmt.where(Message.id < before_id)

    # Count total
    count_stmt = select(func.count()).select_from(
        select(Message).where(Message.conversation_id == conversation_id).subquery()
    )
    total = (await session.execute(count_stmt)).scalar_one()

    stmt = stmt.order_by(Message.id.asc()).limit(limit)
    msgs = (await session.execute(stmt)).scalars().all()

    return {
        "messages": [_msg_dict(m) for m in msgs],
        "total": total,
        "limit": limit,
    }


@router.post("/{conversation_id}/takeover")
async def takeover(
    conversation_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Set conversation status to 'human' (agent paused)."""
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conv.status = "human"
    await session.commit()
    return {"ok": True, "status": "human", "conversation_id": conversation_id}


@router.post("/{conversation_id}/release")
async def release(
    conversation_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Return conversation to AI agent (status=active)."""
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conv.status = "active"
    await session.commit()
    return {"ok": True, "status": "active", "conversation_id": conversation_id}


@router.post("/{conversation_id}/reply")
async def reply(
    conversation_id: int,
    body: ReplyBody,
    session: AsyncSession = Depends(get_session),
):
    """
    Send a human-agent reply on behalf of a staff member.

    Conversation must be in status='human'.
    """
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if conv.status != "human":
        raise HTTPException(
            status_code=400,
            detail=f"Conversation is not in human mode (status={conv.status!r}). "
                   "Use /takeover first.",
        )

    try:
        from app.channels.gateway import ChannelGateway
        gateway = ChannelGateway()
        await gateway.dispatch_and_store(
            channel=conv.channel,
            recipient=conv.waid,
            message=body.message,
            conversation_id=conversation_id,
            session=session,
        )
        await session.commit()
    except Exception as exc:
        logger.error("reply_dispatch_error", conversation_id=conversation_id, error=str(exc))
        raise HTTPException(status_code=500, detail=f"Failed to send message: {exc}") from exc

    return {"ok": True}
