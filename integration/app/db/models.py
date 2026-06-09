"""
SQLAlchemy ORM model stubs.

Phase 2 will add:
  - Conversation: stores chat history per customer/channel.
  - IdempotencyRecord: prevents double-processing of mutating calls.
  - PaymentRequest: tracks M-Pesa STK push requests and callbacks.
  - WebhookEvent: deduplication log for incoming webhooks.
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class IdempotencyRecord(Base):
    """Tracks processed idempotency keys to prevent duplicate mutations."""

    __tablename__ = "idempotency_records"

    id = Column(Integer, primary_key=True)
    key = Column(String(255), unique=True, nullable=False, index=True)
    result_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
