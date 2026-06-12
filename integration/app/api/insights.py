"""
Phase 5: Daily analytics / insights API.

Endpoint (prefix /api/insights):
  GET "/daily" — daily summary for the dashboard.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, Message, PaymentRequest
from app.db.session import get_session

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get("/daily")
async def daily_summary(
    target_date: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """
    Return a daily summary of conversations, messages, and payments.

    target_date: YYYY-MM-DD string (optional). Defaults to today (UTC).
    """
    if target_date:
        parsed = date.fromisoformat(target_date)
    else:
        parsed = date.today()

    day_start = datetime(parsed.year, parsed.month, parsed.day, 0, 0, 0, tzinfo=timezone.utc)
    day_end = datetime(parsed.year, parsed.month, parsed.day, 23, 59, 59, 999999, tzinfo=timezone.utc)

    # ── Conversation status counts (all-time snapshot) ────────────────────────
    conv_status_rows = (
        await session.execute(
            select(Conversation.status, func.count(Conversation.id))
            .group_by(Conversation.status)
        )
    ).all()
    conv_status: dict[str, int] = {}
    for status, cnt in conv_status_rows:
        conv_status[status] = cnt

    conv_total = sum(conv_status.values())
    conv_active = conv_status.get("active", 0)
    conv_human = conv_status.get("human", 0)
    conv_closed = conv_status.get("closed", 0)

    # ── Conversations created today ────────────────────────────────────────────
    new_today = (
        await session.execute(
            select(func.count(Conversation.id)).where(
                Conversation.created_at >= day_start,
                Conversation.created_at <= day_end,
            )
        )
    ).scalar_one()

    # ── Message direction counts for today ────────────────────────────────────
    msg_dir_rows = (
        await session.execute(
            select(Message.direction, func.count(Message.id))
            .where(
                Message.created_at >= day_start,
                Message.created_at <= day_end,
            )
            .group_by(Message.direction)
        )
    ).all()
    msg_counts: dict[str, int] = {}
    for direction, cnt in msg_dir_rows:
        msg_counts[direction] = cnt

    msg_inbound = msg_counts.get("inbound", 0)
    msg_outbound = msg_counts.get("outbound", 0)
    msg_total = msg_inbound + msg_outbound

    # ── PaymentRequest status counts for today ────────────────────────────────
    pay_status_rows = (
        await session.execute(
            select(PaymentRequest.status, func.count(PaymentRequest.id))
            .where(
                PaymentRequest.created_at >= day_start,
                PaymentRequest.created_at <= day_end,
            )
            .group_by(PaymentRequest.status)
        )
    ).all()
    pay_counts: dict[str, int] = {}
    for status, cnt in pay_status_rows:
        pay_counts[status] = cnt

    # ── Completed payment revenue (sum in Python, amount_kes is String) ───────
    completed_amounts = (
        await session.execute(
            select(PaymentRequest.amount_kes).where(
                PaymentRequest.status == "completed",
                PaymentRequest.created_at >= day_start,
                PaymentRequest.created_at <= day_end,
            )
        )
    ).scalars().all()

    total_revenue = Decimal("0.00")
    for amt_str in completed_amounts:
        try:
            total_revenue += Decimal(str(amt_str))
        except (InvalidOperation, TypeError):
            logger.warning("invalid_amount_kes", amount=amt_str)

    return {
        "date": parsed.isoformat(),
        "conversations": {
            "total": conv_total,
            "active": conv_active,
            "human": conv_human,
            "closed": conv_closed,
            "new_today": new_today,
        },
        "messages": {
            "total": msg_total,
            "inbound": msg_inbound,
            "outbound": msg_outbound,
        },
        "payments": {
            "initiated": pay_counts.get("pending", 0),
            "completed": pay_counts.get("completed", 0),
            "failed": pay_counts.get("failed", 0),
            "total_revenue_kes": str(total_revenue.quantize(Decimal("0.01"))),
        },
    }
