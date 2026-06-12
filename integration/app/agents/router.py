"""
AgentRouter — ties ConversationAgent, Orchestrator, and ChannelGateway together.

Entry point called from the WhatsApp webhook BackgroundTask after a message
is stored to the DB.
"""
from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.conversation import AgentResponse, ConversationAgent
from app.agents.orchestrator import Orchestrator
from app.channels.gateway import ChannelGateway
from app.db.models import Conversation

logger = structlog.get_logger(__name__)


def _make_erp_client(settings):
    from app.erp.client import ERPNextClient
    return ERPNextClient(
        base_url=settings.erpnext_base_url,
        api_key=settings.erpnext_api_key,
        api_secret=settings.erpnext_api_secret,
    )


def _make_nim_client(settings):
    from app.agents.nim_client import NimClient
    return NimClient(
        api_key=settings.nim_api_key,
        base_url=settings.nim_base_url,
        model=settings.nim_model,
    )


class AgentRouter:
    """
    Routes one inbound message through the agent loop and dispatches the reply.

    Usage:
        router = AgentRouter()
        await router.handle(conversation_id, waid, user_message, session)
    """

    def __init__(self) -> None:
        from app.config import settings
        self._settings = settings

    async def handle(
        self,
        conversation_id: int,
        waid: str,
        user_message: str,
        session: AsyncSession,
    ) -> None:
        """
        Process one message for a conversation:
          0. Check if conversation is in human mode — if so, skip the agent
          1. Run ConversationAgent → AgentResponse
          2. If order_confirmed → run Orchestrator → compose payment message
          3. Dispatch reply via ChannelGateway
        """
        # ── Human-mode guard ──────────────────────────────────────────────────
        result = await session.execute(
            select(Conversation.status).where(Conversation.id == conversation_id)
        )
        conv_status = result.scalar_one_or_none()
        if conv_status == "human":
            logger.info(
                "agent_skipped_human_mode",
                conversation_id=conversation_id,
                waid=waid,
            )
            return

        erp_client = _make_erp_client(self._settings)
        nim_client = _make_nim_client(self._settings)
        gateway = ChannelGateway()

        async with nim_client:
            agent = ConversationAgent(nim_client, erp_client, self._settings)
            response: AgentResponse = await agent.run(
                conversation_id=conversation_id,
                user_message=user_message,
                session=session,
            )

        logger.info(
            "agent_router_response",
            conversation_id=conversation_id,
            action=response.action,
            tokens=response.total_tokens,
        )

        if response.action == "reply":
            await gateway.dispatch_and_store(
                channel="whatsapp",
                recipient=waid,
                message=response.text,
                conversation_id=conversation_id,
                session=session,
            )

        elif response.action == "order_confirmed":
            await self._handle_order(
                conversation_id=conversation_id,
                waid=waid,
                response=response,
                session=session,
                gateway=gateway,
                erp_client=erp_client,
            )

        elif response.action == "quote_requested":
            text = response.text or (
                "Naomba namba za bidhaa na wingi ili nikusaidie bei sahihi."
            )
            await gateway.dispatch_and_store(
                channel="whatsapp",
                recipient=waid,
                message=text,
                conversation_id=conversation_id,
                session=session,
            )

        elif response.action == "escalate":
            reason = response.data.get("reason", "Customer needs human assistance")
            msg = (
                "Samahani, nitakupelekea mtu wa DEWMIX akusaidie. "
                f"Tafadhali subiri kidogo. ({reason})"
            )
            await gateway.dispatch_and_store(
                channel="whatsapp",
                recipient=waid,
                message=msg,
                conversation_id=conversation_id,
                session=session,
            )

        elif response.action == "error":
            await gateway.dispatch_and_store(
                channel="whatsapp",
                recipient=waid,
                message=response.text or "Samahani, kuna tatizo. Tafadhali jaribu tena.",
                conversation_id=conversation_id,
                session=session,
            )

    async def _handle_order(
        self,
        conversation_id: int,
        waid: str,
        response: AgentResponse,
        session: AsyncSession,
        gateway: ChannelGateway,
        erp_client,
    ) -> None:
        """Run Orchestrator and send the payment prompt."""
        data = response.data
        items = data.get("items", [])
        customer_name = data.get("customer_name")
        delivery_preference = data.get("delivery_preference", "pickup")
        delivery_address = data.get("delivery_address", "")

        orchestrator = Orchestrator(erp_client, self._settings)

        phone = f"+{waid}" if not waid.startswith("+") else waid

        result = await orchestrator.process_order(
            conversation_id=conversation_id,
            phone=phone,
            customer_name=customer_name,
            items=items,
            delivery_preference=delivery_preference,
            delivery_address=delivery_address,
        )

        if result.status == "payment_pending":
            msg = (
                f"Asante! Nimetengeneza ankara yako ({result.invoice_name}).\n"
                f"Jumla: KES {result.grand_total:,.2f}\n\n"
                f"{result.payment_message}\n\n"
                "Ukimaliza kulipa, tutakuarifu."
            )
        else:
            msg = (
                "Samahani, kulikuwa na tatizo kusindika agizo lako. "
                "Mtu wa DEWMIX atakusaidia hivi karibuni."
            )
            logger.error(
                "orchestrator_order_failed",
                conversation_id=conversation_id,
                error=result.error,
            )

        await gateway.dispatch_and_store(
            channel="whatsapp",
            recipient=waid,
            message=msg,
            conversation_id=conversation_id,
            session=session,
        )
