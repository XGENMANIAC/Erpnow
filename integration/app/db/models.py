"""
SQLAlchemy ORM models.
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
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


class PaymentRequest(Base):
    """
    Tracks M-Pesa STK push requests.

    Lifecycle: pending → completed | failed
    """

    __tablename__ = "payment_requests"

    id = Column(Integer, primary_key=True)
    idempotency_key = Column(String(255), unique=True, nullable=False, index=True)
    checkout_request_id = Column(String(255), unique=True, nullable=True, index=True)
    merchant_request_id = Column(String(255), nullable=True)
    invoice_name = Column(String(255), nullable=False, index=True)
    phone = Column(String(20), nullable=False)
    amount_kes = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="pending")  # pending|completed|failed
    mpesa_ref = Column(String(100), nullable=True, index=True)
    erp_payment_entry = Column(String(255), nullable=True)
    result_code = Column(Integer, nullable=True)
    result_desc = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Conversation(Base):
    """
    One conversation = one customer × one channel.

    waid is the sender's WhatsApp ID (phone without +, e.g. "254712345678").
    The combination of (channel, waid) is unique — a customer can have at most
    one active conversation per channel at a time.
    """

    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True)
    channel = Column(String(20), nullable=False, index=True)   # whatsapp | sms | web
    waid = Column(String(30), nullable=False, index=True)      # sender ID on that channel
    display_name = Column(String(255), nullable=True)
    phone = Column(String(20), nullable=True)                  # E.164, e.g. +254712345678
    erp_customer_name = Column(String(255), nullable=True)     # linked ERPNext Customer
    status = Column(String(20), nullable=False, default="active")  # active|closed|human
    last_message_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Message(Base):
    """
    A single message in a conversation (inbound or outbound).

    message_id is the channel-specific ID used for deduplication — WhatsApp
    can deliver the same webhook twice, and we check this field before
    processing.
    """

    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False, index=True)
    message_id = Column(String(255), unique=True, nullable=False, index=True)  # dedup key
    direction = Column(String(10), nullable=False)            # inbound | outbound
    message_type = Column(String(30), nullable=False)         # text|image|audio|...
    body = Column(Text, nullable=True)
    media_id = Column(String(255), nullable=True)
    msg_timestamp = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
