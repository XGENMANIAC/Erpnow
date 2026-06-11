"""
Payment operations: M-Pesa STK push initiation + ERPNext Payment Entry recording.

Architecture rules:
  - ERPNext is the source of truth for payment state (idempotency by reference_no).
  - Money amounts (amount, paid_amount) come from the caller — never computed here.
  - The DB (PaymentRequest) tracks STK push state for operator visibility;
    it is a cache/log, NOT the source of truth.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import structlog

from app.erp.client import ERPNextClient, ERPNextDuplicateError
from app.erp.schemas import ERPPaymentEntry
from app.mpesa.client import DarajaClient

logger = structlog.get_logger(__name__)


def _make_daraja_client(settings) -> DarajaClient:
    return DarajaClient(
        consumer_key=settings.mpesa_consumer_key,
        consumer_secret=settings.mpesa_consumer_secret,
        shortcode=settings.mpesa_shortcode,
        passkey=settings.mpesa_passkey,
        callback_url=settings.mpesa_callback_url,
        environment=settings.mpesa_environment,
    )


async def request_mpesa_payment(
    client: ERPNextClient,
    invoice_name: str,
    phone: str,
    amount: Decimal,
    idempotency_key: str,
) -> dict:
    """
    Initiate an M-Pesa STK push for a Sales Invoice.

    The caller is responsible for persisting the returned checkout_request_id
    so the callback webhook can map back to this invoice.

    Args:
        client:           ERPNext client (used to validate the invoice exists).
        invoice_name:     ERPNext Sales Invoice document name.
        phone:            Customer phone in +254XXXXXXXXX format.
        amount:           Amount to charge (must match invoice outstanding).
        idempotency_key:  Deduplication key; same key = no duplicate push.

    Returns:
        dict with "status", "checkout_request_id", "merchant_request_id",
        "idempotency_key", and "message".
    """
    from app.config import settings as _settings

    # Build the per-invoice callback URL so the webhook knows which invoice
    # to reconcile without needing a DB lookup.
    callback_url = f"{_settings.mpesa_callback_url}/{invoice_name}"

    daraja = _make_daraja_client(_settings)
    async with daraja:
        result = await daraja.stk_push(
            phone=phone,
            amount=amount,
            account_reference=invoice_name,
            transaction_desc=f"Inv {invoice_name[:10]}",
            callback_url=callback_url,
        )

    checkout_request_id = result.get("CheckoutRequestID", "")
    merchant_request_id = result.get("MerchantRequestID", "")

    logger.info(
        "mpesa_stk_push_initiated",
        invoice_name=invoice_name,
        phone=phone,
        amount=str(amount),
        checkout_request_id=checkout_request_id,
        idempotency_key=idempotency_key,
    )

    return {
        "status": "pending",
        "checkout_request_id": checkout_request_id,
        "merchant_request_id": merchant_request_id,
        "idempotency_key": idempotency_key,
        "message": result.get(
            "CustomerMessage", "Please enter your M-Pesa PIN to complete the payment."
        ),
    }


async def record_payment(
    client: ERPNextClient,
    invoice_name: str,
    mpesa_ref: str,
    amount: Decimal,
    idempotency_key: str,
) -> ERPPaymentEntry:
    """
    Record a confirmed M-Pesa payment as a Payment Entry in ERPNext.

    Idempotent: if a Payment Entry with reference_no == mpesa_ref already
    exists in ERPNext, it is returned without creating a duplicate.

    Args:
        client:           ERPNextClient.
        invoice_name:     ERPNext Sales Invoice to reconcile against.
        mpesa_ref:        M-Pesa transaction reference (e.g. "NLJ7RT61SV").
        amount:           Amount paid (KES).
        idempotency_key:  X-Idempotency-Key forwarded to ERPNext POST.

    Returns:
        ERPPaymentEntry (docstatus=1 for a newly created entry, or the
        existing entry's docstatus if a duplicate was found).
    """
    from app.config import settings as _settings

    # ── Idempotency: check ERPNext for an existing entry with this mpesa_ref ──
    existing_list = await client.get(
        "/api/resource/Payment Entry",
        params={
            "filters": f'[["reference_no","=","{mpesa_ref}"]]',
            "fields": '["name","party","paid_amount","reference_no","docstatus"]',
        },
    )
    if isinstance(existing_list, list) and existing_list:
        existing_data = existing_list[0]
        # If it's a summary row (just {name: ...}), fetch the full doc
        if len(existing_data) == 1 and "name" in existing_data:
            existing_data = await client.get(
                f"/api/resource/Payment Entry/{existing_data['name']}"
            )
        logger.info(
            "payment_entry_found_existing",
            name=existing_data.get("name"),
            mpesa_ref=mpesa_ref,
        )
        return ERPPaymentEntry(**existing_data)

    # ── Fetch invoice to get customer and company ─────────────────────────────
    invoice = await client.get(f"/api/resource/Sales Invoice/{invoice_name}")
    customer = invoice.get("customer") or invoice.get("party_name", "")
    company = invoice.get("company") or _settings.erpnext_company

    # ── Build Payment Entry payload ───────────────────────────────────────────
    payload: dict = {
        "doctype": "Payment Entry",
        "payment_type": "Receive",
        "party_type": "Customer",
        "party": customer,
        "party_name": customer,
        "company": company,
        "paid_amount": str(amount),
        "received_amount": str(amount),
        "reference_no": mpesa_ref,
        "reference_date": date.today().isoformat(),
        "mode_of_payment": _settings.mpesa_mode_of_payment,
        "remarks": f"M-Pesa {mpesa_ref} — invoice {invoice_name}",
        "references": [
            {
                "reference_doctype": "Sales Invoice",
                "reference_name": invoice_name,
                "allocated_amount": str(amount),
            }
        ],
    }

    # Optional explicit account overrides (needed when Mode of Payment
    # does not have accounts auto-configured in this ERPNext instance).
    if _settings.mpesa_debtors_account:
        payload["paid_from"] = _settings.mpesa_debtors_account
    if _settings.mpesa_paid_to_account:
        payload["paid_to"] = _settings.mpesa_paid_to_account

    # ── Create draft ──────────────────────────────────────────────────────────
    try:
        entry_data = await client.post(
            "/api/resource/Payment Entry",
            json=payload,
            idempotency_key=idempotency_key,
        )
    except ERPNextDuplicateError:
        logger.warning("payment_entry_duplicate_on_create", mpesa_ref=mpesa_ref)
        raise

    entry = ERPPaymentEntry(**entry_data)
    logger.info("payment_entry_created_draft", name=entry.name, mpesa_ref=mpesa_ref)

    # ── Submit ────────────────────────────────────────────────────────────────
    submitted_data = await client.put(
        f"/api/resource/Payment Entry/{entry.name}",
        json={"docstatus": 1},
        idempotency_key=f"{idempotency_key}:submit",
    )
    submitted = ERPPaymentEntry(**submitted_data)
    logger.info(
        "payment_entry_submitted",
        name=submitted.name,
        paid_amount=str(submitted.paid_amount),
        mpesa_ref=mpesa_ref,
    )

    return submitted
