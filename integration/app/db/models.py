"""
SQLAlchemy ORM models.
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


class PaymentRequest(Base):
    """
    Tracks M-Pesa STK push requests.

    Lifecycle: pending → completed | failed
    The checkout_request_id links this record to the Daraja callback.
    invoice_name is persisted here so the callback handler knows which
    invoice to reconcile when Daraja calls back.
    """

    __tablename__ = "payment_requests"

    id = Column(Integer, primary_key=True)
    idempotency_key = Column(String(255), unique=True, nullable=False, index=True)
    checkout_request_id = Column(String(255), unique=True, nullable=True, index=True)
    merchant_request_id = Column(String(255), nullable=True)
    invoice_name = Column(String(255), nullable=False, index=True)
    phone = Column(String(20), nullable=False)
    amount_kes = Column(String(20), nullable=False)  # stored as string to avoid float
    status = Column(String(20), nullable=False, default="pending")  # pending|completed|failed
    mpesa_ref = Column(String(100), nullable=True, index=True)
    erp_payment_entry = Column(String(255), nullable=True)
    result_code = Column(Integer, nullable=True)
    result_desc = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
