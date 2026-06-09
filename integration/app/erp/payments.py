"""
Payment operations against ERPNext.

Phase 1: Both functions are stubs that raise NotImplementedError.
Phase 2 will implement real M-Pesa STK push and payment entry recording.

The function signatures are final and must not change between phases.
"""
from __future__ import annotations

from decimal import Decimal

from app.erp.client import ERPNextClient
from app.erp.schemas import ERPPaymentEntry


async def request_mpesa_payment(
    client: ERPNextClient,
    invoice_name: str,
    phone: str,
    amount: Decimal,
    idempotency_key: str,
) -> dict:
    """
    Initiate an M-Pesa STK push for a Sales Invoice.

    Phase 2 implementation will:
      1. Call the Safaricom Daraja API to initiate an STK push.
      2. Store the request in the local DB with idempotency_key.
      3. Return a pending state dict; the callback updates the record.

    Args:
        client:           ERPNext client (used in Phase 2 to record the entry).
        invoice_name:     ERPNext Sales Invoice document name.
        phone:            Customer phone in +254XXXXXXXXX format.
        amount:           Amount to charge (must match invoice outstanding).
        idempotency_key:  Deduplication key; same key = no duplicate push.

    Returns:
        dict with "status", "checkout_request_id", and "message".
    """
    raise NotImplementedError("M-Pesa integration coming in Phase 2")


async def record_payment(
    client: ERPNextClient,
    invoice_name: str,
    mpesa_ref: str,
    amount: Decimal,
    idempotency_key: str,
) -> ERPPaymentEntry:
    """
    Record a confirmed M-Pesa payment as a Payment Entry in ERPNext.

    Phase 2 implementation will:
      1. POST a Payment Entry to ERPNext linked to the invoice.
      2. Submit the entry (docstatus=1).
      3. Return the ERPPaymentEntry model.

    Args:
        client:           ERPNext client.
        invoice_name:     ERPNext Sales Invoice to reconcile against.
        mpesa_ref:        M-Pesa transaction reference code.
        amount:           Amount paid (Decimal, KES).
        idempotency_key:  Deduplication key to prevent double-posting.

    Returns:
        ERPPaymentEntry with the created payment record.
    """
    raise NotImplementedError("M-Pesa integration coming in Phase 2")
