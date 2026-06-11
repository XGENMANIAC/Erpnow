"""
Tests for ConversationAgent.

NimClient.run_turn is mocked so these tests cover agent logic, not HTTP calls.
DB is an in-memory SQLite via test_db_session fixture.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.conversation import AgentResponse, ConversationAgent
from app.agents.nim_client import AgentTurn
from app.db.models import Conversation, Message


def _make_agent(turn: AgentTurn) -> ConversationAgent:
    """Return a ConversationAgent with a mocked NimClient that returns `turn`."""
    nim_client = MagicMock()
    nim_client.run_turn = AsyncMock(return_value=turn)

    erp_client = MagicMock()
    settings = MagicMock()
    settings.erpnext_price_list = "Standard Selling"
    settings.erpnext_default_warehouse = "Stores - DX"

    return ConversationAgent(nim_client=nim_client, erp_client=erp_client, settings=settings)


def _text_turn(text: str, tokens: int = 20) -> AgentTurn:
    return AgentTurn(
        final_text=text,
        all_messages=[{"role": "assistant", "content": text}],
        total_tokens=tokens,
        tool_calls_made=0,
    )


def _terminal_turn(tool_name: str, args: dict, tokens: int = 30) -> AgentTurn:
    return AgentTurn(
        final_text="",
        all_messages=[
            {"role": "terminal_tool", "name": tool_name, "arguments": args}
        ],
        total_tokens=tokens,
        tool_calls_made=1,
    )


# ── Text reply ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_simple_text_reply(test_db_session):
    conv = Conversation(channel="whatsapp", waid="254700000001", status="active")
    test_db_session.add(conv)
    await test_db_session.flush()

    agent = _make_agent(_text_turn("Habari yako! Naweza kukusaidia?"))
    response = await agent.run(
        conversation_id=conv.id,
        user_message="Habari",
        session=test_db_session,
    )

    assert response.action == "reply"
    assert "Habari" in response.text
    assert response.total_tokens == 20


@pytest.mark.asyncio
async def test_reply_is_saved_to_db(test_db_session):
    conv = Conversation(channel="whatsapp", waid="254700000002", status="active")
    test_db_session.add(conv)
    await test_db_session.flush()

    agent = _make_agent(_text_turn("Bei ya mabati ni KES 800."))
    await agent.run(
        conversation_id=conv.id,
        user_message="Bei ya mabati?",
        session=test_db_session,
    )

    from sqlalchemy import select
    result = await test_db_session.execute(
        select(Message).where(
            Message.conversation_id == conv.id,
            Message.direction == "outbound",
        )
    )
    saved = result.scalars().first()
    assert saved is not None
    assert "mabati" in saved.body


# ── Terminal tool: order_confirmed ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_terminal_confirm_order_returns_order_confirmed(test_db_session):
    conv = Conversation(channel="whatsapp", waid="254700000003", status="active")
    test_db_session.add(conv)
    await test_db_session.flush()

    items = [{"item_code": "PIPE-001", "quantity": 10}]
    agent = _make_agent(_terminal_turn("confirm_order", {"items": items}))
    response = await agent.run(
        conversation_id=conv.id,
        user_message="Ndiyo, niambie",
        session=test_db_session,
    )

    assert response.action == "order_confirmed"
    assert response.data["items"] == items
    assert response.total_tokens == 30


# ── Terminal tool: request_quote ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_terminal_request_quote_returns_quote_requested(test_db_session):
    conv = Conversation(channel="whatsapp", waid="254700000004", status="active")
    test_db_session.add(conv)
    await test_db_session.flush()

    items = [{"item_code": "CEMENT-001", "quantity": 50}]
    agent = _make_agent(_terminal_turn("request_quote", {"items": items}))
    response = await agent.run(
        conversation_id=conv.id,
        user_message="Nataka bei ya sementi 50 bags",
        session=test_db_session,
    )

    assert response.action == "quote_requested"
    assert response.data["items"] == items


# ── Terminal tool: escalate_to_human ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_terminal_escalate_returns_escalate(test_db_session):
    conv = Conversation(channel="whatsapp", waid="254700000005", status="active")
    test_db_session.add(conv)
    await test_db_session.flush()

    agent = _make_agent(
        _terminal_turn("escalate_to_human", {"reason": "High-value order"})
    )
    response = await agent.run(
        conversation_id=conv.id,
        user_message="Nataka KES 200,000 worth of materials",
        session=test_db_session,
    )

    assert response.action == "escalate"
    assert response.data["reason"] == "High-value order"


# ── NIM error → error response ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nim_error_returns_error_response(test_db_session):
    conv = Conversation(channel="whatsapp", waid="254700000006", status="active")
    test_db_session.add(conv)
    await test_db_session.flush()

    nim_client = MagicMock()
    nim_client.run_turn = AsyncMock(side_effect=RuntimeError("NIM timeout"))
    erp_client = MagicMock()
    settings = MagicMock()
    settings.erpnext_price_list = "Standard Selling"
    settings.erpnext_default_warehouse = "Stores - DX"

    agent = ConversationAgent(nim_client=nim_client, erp_client=erp_client, settings=settings)
    response = await agent.run(
        conversation_id=conv.id,
        user_message="Nataka mabati",
        session=test_db_session,
    )

    assert response.action == "error"
    assert "NIM timeout" in response.data["error"]


# ── Empty final_text falls back to default message ────────────────────────────

@pytest.mark.asyncio
async def test_empty_final_text_gets_fallback(test_db_session):
    conv = Conversation(channel="whatsapp", waid="254700000007", status="active")
    test_db_session.add(conv)
    await test_db_session.flush()

    agent = _make_agent(AgentTurn(final_text="", all_messages=[], total_tokens=5))
    response = await agent.run(
        conversation_id=conv.id,
        user_message="???",
        session=test_db_session,
    )

    assert response.action == "reply"
    assert len(response.text) > 0


# ── History is loaded and included ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_history_is_prepended_to_messages(test_db_session):
    conv = Conversation(channel="whatsapp", waid="254700000008", status="active")
    test_db_session.add(conv)
    await test_db_session.flush()

    # Add an existing inbound message to history
    test_db_session.add(Message(
        conversation_id=conv.id,
        message_id="hist-001",
        direction="inbound",
        message_type="text",
        body="Habari za mabati?",
    ))
    await test_db_session.flush()

    nim_client = MagicMock()
    captured_messages = []

    async def _capture_run_turn(messages, **kwargs):
        captured_messages.extend(messages)
        return _text_turn("Nimekusikia.")

    nim_client.run_turn = _capture_run_turn
    erp_client = MagicMock()
    settings = MagicMock()
    settings.erpnext_price_list = "Standard Selling"
    settings.erpnext_default_warehouse = "Stores - DX"

    agent = ConversationAgent(nim_client=nim_client, erp_client=erp_client, settings=settings)
    await agent.run(
        conversation_id=conv.id,
        user_message="Bei gani?",
        session=test_db_session,
    )

    # system + history (1 user msg) + new user msg = 3
    assert len(captured_messages) == 3
    assert captured_messages[0]["role"] == "system"
    assert captured_messages[1]["content"] == "Habari za mabati?"
    assert captured_messages[2]["content"] == "Bei gani?"


# ── tool_calls_made is forwarded ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_calls_made_forwarded(test_db_session):
    conv = Conversation(channel="whatsapp", waid="254700000009", status="active")
    test_db_session.add(conv)
    await test_db_session.flush()

    turn = AgentTurn(
        final_text="Bei ni KES 1,500.",
        all_messages=[],
        total_tokens=50,
        tool_calls_made=2,
    )
    agent = _make_agent(turn)
    response = await agent.run(
        conversation_id=conv.id,
        user_message="Bei?",
        session=test_db_session,
    )

    assert response.tool_calls_made == 2
