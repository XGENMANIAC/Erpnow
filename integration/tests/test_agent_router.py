"""
Tests for AgentRouter.

ConversationAgent and ChannelGateway are mocked so these tests cover routing
logic without requiring NIM or WhatsApp credentials.
DB is in-memory SQLite via test_db_session fixture.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.conversation import AgentResponse
from app.agents.orchestrator import OrchestratorResult
from app.agents.router import AgentRouter
from app.db.models import Conversation


def _make_router() -> AgentRouter:
    """Return AgentRouter with mocked settings (no live config)."""
    with patch("app.agents.router.AgentRouter.__init__", lambda self: None):
        router = AgentRouter.__new__(AgentRouter)
    router._settings = MagicMock()
    router._settings.erpnext_base_url = "http://erp.test"
    router._settings.erpnext_api_key = "key"
    router._settings.erpnext_api_secret = "sec"
    router._settings.nim_api_key = "nim-key"
    router._settings.nim_base_url = "https://nim.test/v1"
    router._settings.nim_model = "test-model"
    return router


# ── Reply action → dispatch text ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reply_action_dispatches_text(test_db_session):
    conv = Conversation(channel="whatsapp", waid="254700001001", status="active")
    test_db_session.add(conv)
    await test_db_session.flush()

    router = _make_router()
    reply_response = AgentResponse(action="reply", text="Habari yako!")

    with (
        patch("app.agents.router._make_erp_client", return_value=MagicMock()),
        patch("app.agents.router._make_nim_client") as mock_nim_factory,
        patch("app.agents.router.ConversationAgent") as mock_agent_cls,
        patch("app.agents.router.ChannelGateway") as mock_gw_cls,
    ):
        mock_nim = MagicMock()
        mock_nim.__aenter__ = AsyncMock(return_value=mock_nim)
        mock_nim.__aexit__ = AsyncMock(return_value=False)
        mock_nim_factory.return_value = mock_nim

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=reply_response)
        mock_agent_cls.return_value = mock_agent

        mock_gw = MagicMock()
        mock_gw.dispatch_and_store = AsyncMock(return_value={})
        mock_gw_cls.return_value = mock_gw

        await router.handle(
            conversation_id=conv.id,
            waid="254700001001",
            user_message="Habari",
            session=test_db_session,
        )

    mock_gw.dispatch_and_store.assert_awaited_once()
    call_kwargs = mock_gw.dispatch_and_store.call_args[1]
    assert call_kwargs["message"] == "Habari yako!"
    assert call_kwargs["recipient"] == "254700001001"


# ── Order confirmed → Orchestrator runs ───────────────────────────────────────

@pytest.mark.asyncio
async def test_order_confirmed_runs_orchestrator(test_db_session):
    conv = Conversation(channel="whatsapp", waid="254700001002", status="active")
    test_db_session.add(conv)
    await test_db_session.flush()

    router = _make_router()
    order_response = AgentResponse(
        action="order_confirmed",
        text="",
        data={
            "items": [{"item_code": "PIPE-001", "quantity": 5}],
            "customer_name": "John Doe",
        },
    )
    orch_result = OrchestratorResult(
        status="payment_pending",
        invoice_name="SINV-9001",
        sales_order_name="SO-9001",
        grand_total=Decimal("7500.00"),
        payment_message="Check your phone for M-Pesa prompt.",
    )

    with (
        patch("app.agents.router._make_erp_client", return_value=MagicMock()),
        patch("app.agents.router._make_nim_client") as mock_nim_factory,
        patch("app.agents.router.ConversationAgent") as mock_agent_cls,
        patch("app.agents.router.Orchestrator") as mock_orch_cls,
        patch("app.agents.router.ChannelGateway") as mock_gw_cls,
    ):
        mock_nim = MagicMock()
        mock_nim.__aenter__ = AsyncMock(return_value=mock_nim)
        mock_nim.__aexit__ = AsyncMock(return_value=False)
        mock_nim_factory.return_value = mock_nim

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=order_response)
        mock_agent_cls.return_value = mock_agent

        mock_orch = MagicMock()
        mock_orch.process_order = AsyncMock(return_value=orch_result)
        mock_orch_cls.return_value = mock_orch

        mock_gw = MagicMock()
        mock_gw.dispatch_and_store = AsyncMock(return_value={})
        mock_gw_cls.return_value = mock_gw

        await router.handle(
            conversation_id=conv.id,
            waid="254700001002",
            user_message="Ndiyo, niambie",
            session=test_db_session,
        )

    mock_orch.process_order.assert_awaited_once()
    call_kwargs = mock_orch.process_order.call_args[1]
    assert call_kwargs["phone"] == "+254700001002"
    assert call_kwargs["items"] == [{"item_code": "PIPE-001", "quantity": 5}]

    sent_msg = mock_gw.dispatch_and_store.call_args[1]["message"]
    assert "SINV-9001" in sent_msg
    assert "7,500.00" in sent_msg


# ── Order confirmed but Orchestrator fails ────────────────────────────────────

@pytest.mark.asyncio
async def test_order_confirmed_orchestrator_error_sends_apology(test_db_session):
    conv = Conversation(channel="whatsapp", waid="254700001003", status="active")
    test_db_session.add(conv)
    await test_db_session.flush()

    router = _make_router()
    order_response = AgentResponse(
        action="order_confirmed",
        text="",
        data={"items": [{"item_code": "PIPE-001", "quantity": 1}]},
    )
    orch_result = OrchestratorResult(status="error", error="ERP down")

    with (
        patch("app.agents.router._make_erp_client", return_value=MagicMock()),
        patch("app.agents.router._make_nim_client") as mock_nim_factory,
        patch("app.agents.router.ConversationAgent") as mock_agent_cls,
        patch("app.agents.router.Orchestrator") as mock_orch_cls,
        patch("app.agents.router.ChannelGateway") as mock_gw_cls,
    ):
        mock_nim = MagicMock()
        mock_nim.__aenter__ = AsyncMock(return_value=mock_nim)
        mock_nim.__aexit__ = AsyncMock(return_value=False)
        mock_nim_factory.return_value = mock_nim

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=order_response)
        mock_agent_cls.return_value = mock_agent

        mock_orch = MagicMock()
        mock_orch.process_order = AsyncMock(return_value=orch_result)
        mock_orch_cls.return_value = mock_orch

        mock_gw = MagicMock()
        mock_gw.dispatch_and_store = AsyncMock(return_value={})
        mock_gw_cls.return_value = mock_gw

        await router.handle(
            conversation_id=conv.id,
            waid="254700001003",
            user_message="Ndiyo",
            session=test_db_session,
        )

    sent_msg = mock_gw.dispatch_and_store.call_args[1]["message"]
    assert "Samahani" in sent_msg or "tatizo" in sent_msg


# ── Escalate action ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_escalate_action_sends_escalation_message(test_db_session):
    conv = Conversation(channel="whatsapp", waid="254700001004", status="active")
    test_db_session.add(conv)
    await test_db_session.flush()

    router = _make_router()
    escalate_response = AgentResponse(
        action="escalate",
        text="",
        data={"reason": "High-value order"},
    )

    with (
        patch("app.agents.router._make_erp_client", return_value=MagicMock()),
        patch("app.agents.router._make_nim_client") as mock_nim_factory,
        patch("app.agents.router.ConversationAgent") as mock_agent_cls,
        patch("app.agents.router.ChannelGateway") as mock_gw_cls,
    ):
        mock_nim = MagicMock()
        mock_nim.__aenter__ = AsyncMock(return_value=mock_nim)
        mock_nim.__aexit__ = AsyncMock(return_value=False)
        mock_nim_factory.return_value = mock_nim

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=escalate_response)
        mock_agent_cls.return_value = mock_agent

        mock_gw = MagicMock()
        mock_gw.dispatch_and_store = AsyncMock(return_value={})
        mock_gw_cls.return_value = mock_gw

        await router.handle(
            conversation_id=conv.id,
            waid="254700001004",
            user_message="Nataka mkopo",
            session=test_db_session,
        )

    sent_msg = mock_gw.dispatch_and_store.call_args[1]["message"]
    assert "DEWMIX" in sent_msg
    assert "High-value order" in sent_msg


# ── Error action ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_error_action_sends_error_message(test_db_session):
    conv = Conversation(channel="whatsapp", waid="254700001005", status="active")
    test_db_session.add(conv)
    await test_db_session.flush()

    router = _make_router()
    error_response = AgentResponse(
        action="error",
        text="Samahani, kuna hitilafu ya kiufundi. Tafadhali jaribu tena.",
        data={"error": "timeout"},
    )

    with (
        patch("app.agents.router._make_erp_client", return_value=MagicMock()),
        patch("app.agents.router._make_nim_client") as mock_nim_factory,
        patch("app.agents.router.ConversationAgent") as mock_agent_cls,
        patch("app.agents.router.ChannelGateway") as mock_gw_cls,
    ):
        mock_nim = MagicMock()
        mock_nim.__aenter__ = AsyncMock(return_value=mock_nim)
        mock_nim.__aexit__ = AsyncMock(return_value=False)
        mock_nim_factory.return_value = mock_nim

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=error_response)
        mock_agent_cls.return_value = mock_agent

        mock_gw = MagicMock()
        mock_gw.dispatch_and_store = AsyncMock(return_value={})
        mock_gw_cls.return_value = mock_gw

        await router.handle(
            conversation_id=conv.id,
            waid="254700001005",
            user_message="test",
            session=test_db_session,
        )

    sent_msg = mock_gw.dispatch_and_store.call_args[1]["message"]
    assert "hitilafu" in sent_msg or "Samahani" in sent_msg


# ── waid without + gets + prepended for phone ─────────────────────────────────

@pytest.mark.asyncio
async def test_phone_gets_plus_prefix_for_orchestrator(test_db_session):
    conv = Conversation(channel="whatsapp", waid="254700001006", status="active")
    test_db_session.add(conv)
    await test_db_session.flush()

    router = _make_router()
    order_response = AgentResponse(
        action="order_confirmed",
        text="",
        data={"items": [{"item_code": "X", "quantity": 1}]},
    )
    orch_result = OrchestratorResult(
        status="payment_pending",
        invoice_name="INV-X",
        grand_total=Decimal("100"),
        payment_message="Pay now.",
    )

    with (
        patch("app.agents.router._make_erp_client", return_value=MagicMock()),
        patch("app.agents.router._make_nim_client") as mock_nim_factory,
        patch("app.agents.router.ConversationAgent") as mock_agent_cls,
        patch("app.agents.router.Orchestrator") as mock_orch_cls,
        patch("app.agents.router.ChannelGateway") as mock_gw_cls,
    ):
        mock_nim = MagicMock()
        mock_nim.__aenter__ = AsyncMock(return_value=mock_nim)
        mock_nim.__aexit__ = AsyncMock(return_value=False)
        mock_nim_factory.return_value = mock_nim

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=order_response)
        mock_agent_cls.return_value = mock_agent

        mock_orch = MagicMock()
        mock_orch.process_order = AsyncMock(return_value=orch_result)
        mock_orch_cls.return_value = mock_orch

        mock_gw = MagicMock()
        mock_gw.dispatch_and_store = AsyncMock(return_value={})
        mock_gw_cls.return_value = mock_gw

        await router.handle(
            conversation_id=conv.id,
            waid="254700001006",
            user_message="confirm",
            session=test_db_session,
        )

    phone_used = mock_orch.process_order.call_args[1]["phone"]
    assert phone_used == "+254700001006"
