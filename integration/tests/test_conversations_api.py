"""
Tests for the conversations REST API (Phase 5).

Uses httpx.AsyncClient against the FastAPI app with an in-memory SQLite DB.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio

from app.db.models import Conversation, Message
from app.db.session import get_session
from app.main import app


@pytest_asyncio.fixture
async def db(test_db_session):
    """Override get_session to use the in-memory SQLite session."""
    async def _fake():
        yield test_db_session

    app.dependency_overrides[get_session] = _fake
    yield test_db_session
    app.dependency_overrides.pop(get_session, None)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_conv(waid: str, status: str = "active", display_name: str = "Test") -> Conversation:
    return Conversation(channel="whatsapp", waid=waid, display_name=display_name, status=status)


def _make_msg(conv_id: int, body: str, direction: str = "inbound", uid: str | None = None) -> Message:
    return Message(
        conversation_id=conv_id,
        message_id=uid or f"msg-{body[:6]}-{conv_id}",
        direction=direction,
        message_type="text",
        body=body,
    )


# ── List conversations ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_conversations_empty(db):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/conversations")
    assert resp.status_code == 200
    data = resp.json()
    assert data["conversations"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_conversations_returns_data(db):
    c1 = _make_conv("254700010001", display_name="Alice")
    c2 = _make_conv("254700010002", display_name="Bob")
    db.add_all([c1, c2])
    await db.flush()
    db.add(_make_msg(c1.id, "Nataka mabati", uid="msg-001"))
    await db.flush()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/conversations")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["conversations"]) == 2
    # Find Alice's conv and check last_message
    alice = next(c for c in data["conversations"] if c["display_name"] == "Alice")
    assert alice["last_message"] == "Nataka mabati"


@pytest.mark.asyncio
async def test_list_conversations_filters_by_status(db):
    db.add_all([
        _make_conv("254700010003", status="active"),
        _make_conv("254700010004", status="human"),
    ])
    await db.flush()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/conversations?status=human")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["conversations"][0]["status"] == "human"


@pytest.mark.asyncio
async def test_list_conversations_pagination(db):
    for i in range(5):
        db.add(_make_conv(f"25470001{i:04d}"))
    await db.flush()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/conversations?limit=2&offset=0")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["conversations"]) == 2
    assert data["total"] == 5


# ── Get single conversation ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_conversation_not_found(db):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/conversations/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_conversation_exists(db):
    conv = _make_conv("254700010010", display_name="Charlie")
    db.add(conv)
    await db.flush()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/conversations/{conv.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["display_name"] == "Charlie"
    assert data["waid"] == "254700010010"
    assert data["status"] == "active"


# ── Messages ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_messages_empty(db):
    conv = _make_conv("254700010020")
    db.add(conv)
    await db.flush()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/conversations/{conv.id}/messages")
    assert resp.status_code == 200
    data = resp.json()
    assert data["messages"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_get_messages_returns_data_ascending(db):
    conv = _make_conv("254700010021")
    db.add(conv)
    await db.flush()
    db.add(_make_msg(conv.id, "First message", uid="msg-f1"))
    await db.flush()
    db.add(_make_msg(conv.id, "Second message", uid="msg-f2"))
    await db.flush()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/conversations/{conv.id}/messages")
    assert resp.status_code == 200
    msgs = resp.json()["messages"]
    assert len(msgs) == 2
    assert msgs[0]["body"] == "First message"
    assert msgs[1]["body"] == "Second message"


@pytest.mark.asyncio
async def test_get_messages_conversation_not_found(db):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/conversations/88888/messages")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_messages_before_id_cursor(db):
    conv = _make_conv("254700010022")
    db.add(conv)
    await db.flush()
    for i in range(3):
        db.add(_make_msg(conv.id, f"Message {i}", uid=f"msg-cur-{i}"))
    await db.flush()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        # Get all to find IDs
        all_resp = await ac.get(f"/api/conversations/{conv.id}/messages")
    all_msgs = all_resp.json()["messages"]
    last_id = all_msgs[-1]["id"]

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/conversations/{conv.id}/messages?before_id={last_id}")
    data = resp.json()
    # Should return 2 messages before the last one
    assert len(data["messages"]) == 2
    for m in data["messages"]:
        assert m["id"] < last_id


# ── Takeover ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_takeover_sets_human_status(db):
    conv = _make_conv("254700010030", status="active")
    db.add(conv)
    await db.flush()
    conv_id = conv.id

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(f"/api/conversations/{conv_id}/takeover")
    assert resp.status_code == 200
    assert resp.json()["status"] == "human"

    await db.refresh(conv)
    assert conv.status == "human"


@pytest.mark.asyncio
async def test_takeover_not_found(db):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/conversations/77777/takeover")
    assert resp.status_code == 404


# ── Release ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_release_sets_active_status(db):
    conv = _make_conv("254700010040", status="human")
    db.add(conv)
    await db.flush()
    conv_id = conv.id

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(f"/api/conversations/{conv_id}/release")
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"

    await db.refresh(conv)
    assert conv.status == "active"


# ── Human reply ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reply_not_in_human_mode_returns_400(db):
    conv = _make_conv("254700010050", status="active")
    db.add(conv)
    await db.flush()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/conversations/{conv.id}/reply",
            json={"message": "Hello!"},
        )
    assert resp.status_code == 400
    assert "human mode" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_reply_sends_message(db):
    conv = _make_conv("254700010060", status="human")
    db.add(conv)
    await db.flush()

    with patch(
        "app.channels.gateway.ChannelGateway.dispatch_and_store",
        new=AsyncMock(return_value={"messages": [{"id": "wamid.REPLY001"}]}),
    ) as mock_dispatch:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/conversations/{conv.id}/reply",
                json={"message": "Karibu DEWMIX!"},
            )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_dispatch.assert_awaited_once()
    call_kwargs = mock_dispatch.call_args[1]
    assert call_kwargs["message"] == "Karibu DEWMIX!"
    assert call_kwargs["recipient"] == "254700010060"


@pytest.mark.asyncio
async def test_reply_conversation_not_found(db):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/conversations/66666/reply", json={"message": "Hi"})
    assert resp.status_code == 404
