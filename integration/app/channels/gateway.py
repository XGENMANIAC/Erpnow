"""
Channel gateway — routes inbound messages to storage and outbound messages
to the appropriate channel sender.

Phase 3 responsibilities:
  - parse inbound webhooks → InboundMessage list
  - deduplicate by message_id (unique DB constraint)
  - upsert Conversation; store Message
  - dispatch outbound text messages via WhatsApp

Phase 4 will add agent routing on top of this.
"""
from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.whatsapp import InboundMessage, WhatsAppClient, parse_inbound_webhook
from app.config import settings
from app.db.models import Conversation, Message

logger = structlog.get_logger(__name__)


class ChannelGateway:
    """Routes inbound and outbound messages across channels."""

    # ── Outbound ──────────────────────────────────────────────────────────────

    def _make_whatsapp_client(self) -> WhatsAppClient:
        return WhatsAppClient(
            access_token=settings.whatsapp_access_token,
            phone_number_id=settings.whatsapp_phone_number_id,
        )

    async def dispatch(self, channel: str, recipient: str, message: str) -> dict:
        """
        Send an outbound text message via the specified channel.

        Returns the channel API response dict.
        """
        if channel != "whatsapp":
            raise ValueError(f"Unsupported channel: {channel!r}")

        async with self._make_whatsapp_client() as wa:
            result = await wa.send_text(to=recipient, text=message)

        return result

    async def dispatch_and_store(
        self,
        channel: str,
        recipient: str,
        message: str,
        conversation_id: int,
        session: AsyncSession,
    ) -> dict:
        """
        Send an outbound message and persist it to the messages table.

        Use this variant when the conversation_id is already known
        (e.g. when an agent replies to an ongoing conversation).
        """
        result = await self.dispatch(channel, recipient, message)

        msg_id = (result.get("messages") or [{}])[0].get("id", "")
        outbound = Message(
            conversation_id=conversation_id,
            message_id=msg_id or f"out:{conversation_id}:{hash(message)}",
            direction="outbound",
            message_type="text",
            body=message,
        )
        session.add(outbound)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            logger.warning("outbound_message_dedup", message_id=msg_id)

        return result

    # ── Inbound ───────────────────────────────────────────────────────────────

    async def handle_inbound(
        self,
        channel: str,
        payload: dict,
        session: AsyncSession,
    ) -> list[dict]:
        """
        Process an inbound webhook payload.

        1. Parse the payload into InboundMessage list.
        2. For each message: dedup, upsert Conversation, store Message.
        3. Return a list of summary dicts for processed (non-duplicate) messages.

        Always commits or rollbacks; the session is left in a clean state.
        """
        if channel == "whatsapp":
            messages = parse_inbound_webhook(payload)
        else:
            raise ValueError(f"Unsupported channel: {channel!r}")

        processed: list[dict] = []

        for msg in messages:
            summary = await self._process_one(msg, channel, session)
            if summary:
                processed.append(summary)

        await session.commit()
        return processed

    async def _process_one(
        self,
        msg: InboundMessage,
        channel: str,
        session: AsyncSession,
    ) -> dict | None:
        """
        Persist one inbound message; return a summary dict, or None if duplicate.
        """
        # ── Dedup ─────────────────────────────────────────────────────────────
        existing = await session.execute(
            select(Message.id).where(Message.message_id == msg.message_id)
        )
        if existing.scalar_one_or_none() is not None:
            logger.info("whatsapp_message_dedup", message_id=msg.message_id)
            return None

        # ── Upsert conversation ────────────────────────────────────────────────
        conv = await _upsert_conversation(session, msg, channel)

        # ── Store message ──────────────────────────────────────────────────────
        db_msg = Message(
            conversation_id=conv.id,
            message_id=msg.message_id,
            direction="inbound",
            message_type=msg.message_type,
            body=msg.text,
            media_id=msg.media_id,
            msg_timestamp=msg.timestamp,
        )
        session.add(db_msg)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            logger.info("whatsapp_message_dedup_race", message_id=msg.message_id)
            return None

        logger.info(
            "whatsapp_message_stored",
            message_id=msg.message_id,
            waid=msg.waid,
            type=msg.message_type,
            conversation_id=conv.id,
        )

        return {
            "message_id": msg.message_id,
            "waid": msg.waid,
            "display_name": msg.display_name,
            "type": msg.message_type,
            "text": msg.text,
            "conversation_id": conv.id,
        }


async def _upsert_conversation(
    session: AsyncSession,
    msg: InboundMessage,
    channel: str,
) -> Conversation:
    """Return the existing Conversation for this sender, or create one."""
    result = await session.execute(
        select(Conversation).where(
            Conversation.channel == channel,
            Conversation.waid == msg.waid,
        )
    )
    conv = result.scalar_one_or_none()

    if conv is None:
        conv = Conversation(
            channel=channel,
            waid=msg.waid,
            display_name=msg.display_name,
            phone=f"+{msg.waid}",
            status="active",
            last_message_at=msg.timestamp,
        )
        session.add(conv)
        await session.flush()
        logger.info("conversation_created", waid=msg.waid, channel=channel, id=conv.id)
    else:
        if msg.display_name and conv.display_name != msg.display_name:
            conv.display_name = msg.display_name
        conv.last_message_at = msg.timestamp
        await session.flush()

    return conv
