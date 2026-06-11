"""
M-Pesa Daraja STK-push callback webhook.

Safaricom POSTs the result to:
  POST /api/mpesa/callback/{invoice_name}

The invoice name is embedded in the callback URL when the STK push is
initiated (in request_mpesa_payment), so no DB look-up is needed here.

Idempotency: record_payment checks ERPNext for an existing Payment Entry
with the same M-Pesa reference number before creating one.  Delivering
the same callback twice is therefore safe.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PaymentRequest
from app.db.session import get_session_factory
from app.erp.client import ERPNextClient
from app.erp.payments import record_payment
from app.mpesa.models import parse_stk_callback
from app.config import settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/mpesa", tags=["mpesa"])


def _erp_client() -> ERPNextClient:
    return ERPNextClient(
        base_url=settings.erpnext_base_url,
        api_key=settings.erpnext_api_key,
        api_secret=settings.erpnext_api_secret,
    )


# ── Callback endpoint ─────────────────────────────────────────────────────────

@router.post("/callback/{invoice_name}")
async def mpesa_callback(invoice_name: str, request: Request) -> JSONResponse:
    """
    Receive an M-Pesa STK push result from Safaricom Daraja.

    Always returns HTTP 200 — Safaricom retries non-200 responses, which
    would cause duplicate payment recording.
    """
    # ── Parse body ────────────────────────────────────────────────────────────
    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("mpesa_callback_invalid_json", error=str(exc))
        return JSONResponse({"ResultCode": 0, "ResultDesc": "Accepted"})

    logger.info("mpesa_callback_received", invoice_name=invoice_name)

    try:
        callback = parse_stk_callback(body)
    except Exception as exc:
        logger.error("mpesa_callback_parse_error", error=str(exc), invoice_name=invoice_name)
        return JSONResponse({"ResultCode": 0, "ResultDesc": "Accepted"})

    # ── Update DB record (best-effort) ────────────────────────────────────────
    try:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(PaymentRequest).where(
                    PaymentRequest.checkout_request_id == callback.checkout_request_id
                )
            )
            pr = result.scalar_one_or_none()
            if pr:
                pr.result_code = callback.result_code
                pr.result_desc = callback.result_desc
                pr.mpesa_ref = callback.mpesa_ref
                pr.status = "completed" if callback.is_success else "failed"
                await session.commit()
    except Exception as exc:
        logger.warning("mpesa_callback_db_update_failed", error=str(exc))

    # ── Failed payment ────────────────────────────────────────────────────────
    if not callback.is_success:
        logger.warning(
            "mpesa_payment_failed",
            invoice_name=invoice_name,
            result_code=callback.result_code,
            result_desc=callback.result_desc,
            checkout_request_id=callback.checkout_request_id,
        )
        return JSONResponse({"ResultCode": 0, "ResultDesc": "Accepted"})

    # ── Record payment in ERPNext ─────────────────────────────────────────────
    idempotency_key = f"mpesa:{callback.checkout_request_id}"

    try:
        async with _erp_client() as erp:
            entry = await record_payment(
                client=erp,
                invoice_name=invoice_name,
                mpesa_ref=callback.mpesa_ref,  # type: ignore[arg-type]
                amount=callback.amount,  # type: ignore[arg-type]
                idempotency_key=idempotency_key,
            )

        logger.info(
            "mpesa_payment_recorded",
            invoice_name=invoice_name,
            payment_entry=entry.name,
            mpesa_ref=callback.mpesa_ref,
        )

        # ── Update DB with payment entry name ─────────────────────────────────
        try:
            factory = get_session_factory()
            async with factory() as session:
                result = await session.execute(
                    select(PaymentRequest).where(
                        PaymentRequest.checkout_request_id == callback.checkout_request_id
                    )
                )
                pr = result.scalar_one_or_none()
                if pr:
                    pr.erp_payment_entry = entry.name
                    await session.commit()
        except Exception as exc:
            logger.warning("mpesa_callback_db_entry_update_failed", error=str(exc))

    except Exception as exc:
        logger.error(
            "mpesa_payment_record_failed",
            invoice_name=invoice_name,
            error=str(exc),
            mpesa_ref=callback.mpesa_ref,
        )

    return JSONResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


# ── Initiate STK push (API endpoint) ─────────────────────────────────────────

@router.post("/pay/{invoice_name}")
async def initiate_payment(invoice_name: str, request: Request) -> JSONResponse:
    """
    Initiate an M-Pesa STK push for an invoice.

    Request body: {"phone": "+254XXXXXXXXX", "amount": "406.00"}
    """
    from decimal import Decimal
    from app.erp.payments import request_mpesa_payment

    try:
        body = await request.json()
        phone: str = body["phone"]
        amount = Decimal(str(body["amount"]))
    except Exception as exc:
        return JSONResponse({"error": f"Invalid request: {exc}"}, status_code=400)

    idempotency_key = f"pay:{invoice_name}:{phone}"

    try:
        async with _erp_client() as erp:
            result = await request_mpesa_payment(
                client=erp,
                invoice_name=invoice_name,
                phone=phone,
                amount=amount,
                idempotency_key=idempotency_key,
            )
    except Exception as exc:
        logger.error("mpesa_initiate_failed", invoice_name=invoice_name, error=str(exc))
        return JSONResponse({"error": str(exc)}, status_code=502)

    # Persist to DB for operator visibility (best-effort)
    try:
        factory = get_session_factory()
        async with factory() as session:
            pr = PaymentRequest(
                idempotency_key=idempotency_key,
                checkout_request_id=result.get("checkout_request_id"),
                merchant_request_id=result.get("merchant_request_id"),
                invoice_name=invoice_name,
                phone=phone,
                amount_kes=str(amount),
                status="pending",
            )
            session.add(pr)
            await session.commit()
    except Exception as exc:
        logger.warning("mpesa_pay_db_persist_failed", error=str(exc))

    return JSONResponse(result)
