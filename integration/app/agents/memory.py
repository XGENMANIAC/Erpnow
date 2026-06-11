"""
Conversation memory helpers.

Loads message history from the DB and formats it for the LLM.
Persists agent (outbound) messages back to the DB.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message


async def load_history(
    conversation_id: int,
    session: AsyncSession,
    limit: int = 20,
) -> list[dict]:
    """
    Return the last `limit` messages from this conversation as
    OpenAI-format role/content dicts, oldest-first.

    Only inbound (user) and outbound text (assistant) messages are included;
    internal LLM tool exchanges are not stored individually.
    """
    result = await session.execute(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.message_type.in_(("text", "interactive", "button")),
        )
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    rows = list(reversed(result.scalars().all()))

    history: list[dict] = []
    for msg in rows:
        if msg.direction == "inbound":
            history.append({"role": "user", "content": msg.body or ""})
        elif msg.direction == "outbound":
            history.append({"role": "assistant", "content": msg.body or ""})
    return history


async def save_assistant_reply(
    conversation_id: int,
    text: str,
    session: AsyncSession,
) -> None:
    """Persist an agent (outbound) text reply to the messages table."""
    import hashlib
    msg_id = f"agent:{conversation_id}:{hashlib.md5(text.encode()).hexdigest()[:8]}"
    msg = Message(
        conversation_id=conversation_id,
        message_id=msg_id,
        direction="outbound",
        message_type="text",
        body=text,
    )
    session.add(msg)
    await session.flush()
