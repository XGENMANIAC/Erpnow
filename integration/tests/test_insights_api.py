"""
Tests for the insights REST API (Phase 5).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
import pytest
import pytest_asyncio

from app.db.models import Conversation, Message, PaymentRequest
from app.db.session import get_session
from app.main import app


@pytest_asyncio.fixture
async def db(test_db_session):
    async def _fake():
        yield test_db_session

    app.dependency_overrides[get_session] = _fake
    yield test_db_session
    app.dependency_overrides.pop(get_session, None)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_conv(waid: str, status: str = "active") -> Conversation:
    return Conversation(channel="whatsapp", waid=waid, status=status)


def _make_msg(conv_id: int, direction: str = "inbound", uid: str | None = None) -> Message:
    return Message(
        conversation_id=conv_id,
        message_id=uid or f"ins-{conv_id}-{direction[:2]}",
        direction=direction,
        message_type="text",
        body="test",
    )


def _make_payment(invoice: str, status: str, amount: str, uid: str) -> PaymentRequest:
    return PaymentRequest(
        idempotency_key=uid,
        invoice_name=invoice,
        phone="+254700000000",
        amount_kes=amount,
        status=status,
    )


# ── Basic ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_daily_summary_empty_db(db):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/insights/daily")
    assert resp.status_code == 200
    data = resp.json()
    assert data["conversations"]["total"] == 0
    assert data["messages"]["total"] == 0
    assert data["payments"]["completed"] == 0
    assert data["payments"]["total_revenue_kes"] == "0.00"


@pytest.mark.asyncio
async def test_daily_summary_date_in_response(db):
    today = date.today().isoformat()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/insights/daily")
    assert resp.json()["date"] == today


@pytest.mark.asyncio
async def test_daily_summary_custom_date(db):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/insights/daily?target_date=2024-06-01")
    assert resp.status_code == 200
    assert resp.json()["date"] == "2024-06-01"


# ── Conversation counts ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_daily_summary_counts_conversations_by_status(db):
    db.add_all([
        _make_conv("254700020001", status="active"),
        _make_conv("254700020002", status="active"),
        _make_conv("254700020003", status="human"),
        _make_conv("254700020004", status="closed"),
    ])
    await db.flush()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/insights/daily")
    data = resp.json()["conversations"]
    assert data["total"] == 4
    assert data["active"] == 2
    assert data["human"] == 1
    assert data["closed"] == 1


# ── Message counts ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_daily_summary_counts_messages(db):
    conv = _make_conv("254700020010")
    db.add(conv)
    await db.flush()
    db.add(_make_msg(conv.id, "inbound", "ins-in-1"))
    db.add(_make_msg(conv.id, "inbound", "ins-in-2"))
    db.add(_make_msg(conv.id, "outbound", "ins-out-1"))
    await db.flush()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/insights/daily")
    msgs = resp.json()["messages"]
    assert msgs["inbound"] == 2
    assert msgs["outbound"] == 1
    assert msgs["total"] == 3


# ── Payment counts and revenue ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_daily_summary_payment_counts(db):
    db.add_all([
        _make_payment("INV-001", "pending", "1000.00", "ik-p1"),
        _make_payment("INV-002", "completed", "2000.00", "ik-p2"),
        _make_payment("INV-003", "completed", "3500.00", "ik-p3"),
        _make_payment("INV-004", "failed", "500.00", "ik-p4"),
    ])
    await db.flush()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/insights/daily")
    pay = resp.json()["payments"]
    assert pay["initiated"] == 1
    assert pay["completed"] == 2
    assert pay["failed"] == 1
    assert pay["total_revenue_kes"] == "5500.00"


@pytest.mark.asyncio
async def test_daily_summary_revenue_zero_when_no_completed(db):
    db.add(_make_payment("INV-005", "pending", "999.00", "ik-p5"))
    await db.flush()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/insights/daily")
    assert resp.json()["payments"]["total_revenue_kes"] == "0.00"


@pytest.mark.asyncio
async def test_daily_summary_revenue_decimal_precision(db):
    db.add(_make_payment("INV-006", "completed", "1234.56", "ik-p6"))
    await db.flush()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/insights/daily")
    assert resp.json()["payments"]["total_revenue_kes"] == "1234.56"
