"""
Tests for the M-Pesa Daraja callback webhook endpoint.

Uses FastAPI TestClient (synchronous wrapper) so no live services needed.
record_payment is patched so ERPNext calls are not made.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.erp.schemas import ERPPaymentEntry
from app.main import app

INVOICE_NAME = "ACC-SINV-2026-00001"
MPESA_REF = "NLJ7RT61SV"
CHECKOUT_ID = "ws_CO_123456789"
MERCHANT_ID = "MR_987654321"

PAYMENT_ENTRY = ERPPaymentEntry(
    name="PE-0001",
    party="CUST-0001",
    paid_amount=Decimal("406.00"),
    reference_no=MPESA_REF,
    docstatus=1,
)


def _success_callback(
    invoice_name: str = INVOICE_NAME,
    checkout_id: str = CHECKOUT_ID,
    merchant_id: str = MERCHANT_ID,
    mpesa_ref: str = MPESA_REF,
    amount: int = 406,
    phone: int = 254712345678,
) -> dict:
    return {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": merchant_id,
                "CheckoutRequestID": checkout_id,
                "ResultCode": 0,
                "ResultDesc": "The service request is processed successfully.",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": amount},
                        {"Name": "MpesaReceiptNumber", "Value": mpesa_ref},
                        {"Name": "Balance"},
                        {"Name": "TransactionDate", "Value": 20260101120000},
                        {"Name": "PhoneNumber", "Value": phone},
                    ]
                },
            }
        }
    }


def _failed_callback(
    result_code: int = 1032,
    result_desc: str = "Request cancelled by user",
) -> dict:
    return {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": MERCHANT_ID,
                "CheckoutRequestID": CHECKOUT_ID,
                "ResultCode": result_code,
                "ResultDesc": result_desc,
            }
        }
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_record_payment(return_value=PAYMENT_ENTRY):
    """Patch record_payment in the webhook module."""
    mock = AsyncMock(return_value=return_value)
    return patch("app.api.mpesa_webhook.record_payment", mock), mock


def _mock_db():
    """Patch the DB session so tests don't need a real DB."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    mock_session.commit = AsyncMock()
    mock_session.add = MagicMock()

    mock_factory = MagicMock(return_value=mock_session)
    return patch("app.api.mpesa_webhook.get_session_factory", return_value=mock_factory)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_callback_success_calls_record_payment():
    """Successful Daraja callback triggers record_payment."""
    rp_patch, rp_mock = _mock_record_payment()
    db_patch = _mock_db()

    with rp_patch, db_patch, TestClient(app) as client:
        resp = client.post(
            f"/api/mpesa/callback/{INVOICE_NAME}",
            json=_success_callback(),
        )

    assert resp.status_code == 200
    assert resp.json() == {"ResultCode": 0, "ResultDesc": "Accepted"}
    rp_mock.assert_awaited_once()

    call_kwargs = rp_mock.call_args
    assert call_kwargs.kwargs["invoice_name"] == INVOICE_NAME
    assert call_kwargs.kwargs["mpesa_ref"] == MPESA_REF
    assert call_kwargs.kwargs["amount"] == Decimal("406")


def test_callback_failed_payment_does_not_call_record_payment():
    """A failed payment (user cancelled) should NOT record a Payment Entry."""
    rp_patch, rp_mock = _mock_record_payment()
    db_patch = _mock_db()

    with rp_patch, db_patch, TestClient(app) as client:
        resp = client.post(
            f"/api/mpesa/callback/{INVOICE_NAME}",
            json=_failed_callback(result_code=1032, result_desc="Request cancelled by user"),
        )

    assert resp.status_code == 200
    assert resp.json() == {"ResultCode": 0, "ResultDesc": "Accepted"}
    rp_mock.assert_not_awaited()


def test_callback_invalid_json_returns_200():
    """Malformed body must not crash; always return 200 to prevent Daraja retries."""
    with TestClient(app) as client:
        resp = client.post(
            f"/api/mpesa/callback/{INVOICE_NAME}",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )

    assert resp.status_code == 200


def test_callback_missing_body_keys_returns_200():
    """Partially structured body should not raise unhandled exceptions."""
    with TestClient(app) as client:
        resp = client.post(
            f"/api/mpesa/callback/{INVOICE_NAME}",
            json={"Body": {}},
        )

    assert resp.status_code == 200


def test_callback_idempotency_key_includes_checkout_id():
    """The idempotency key passed to record_payment should include CheckoutRequestID."""
    rp_patch, rp_mock = _mock_record_payment()
    db_patch = _mock_db()

    with rp_patch, db_patch, TestClient(app) as client:
        client.post(
            f"/api/mpesa/callback/{INVOICE_NAME}",
            json=_success_callback(checkout_id="ws_CO_UNIQUE"),
        )

    call_kwargs = rp_mock.call_args.kwargs
    assert "ws_CO_UNIQUE" in call_kwargs["idempotency_key"]


def test_callback_same_request_twice_calls_record_payment_twice():
    """
    The webhook calls record_payment each time it receives a callback.
    record_payment itself is idempotent (checks ERPNext for existing ref).
    We verify the webhook does NOT swallow the second call.
    """
    rp_patch, rp_mock = _mock_record_payment()
    db_patch = _mock_db()

    cb = _success_callback()
    with rp_patch, db_patch, TestClient(app) as client:
        client.post(f"/api/mpesa/callback/{INVOICE_NAME}", json=cb)
        client.post(f"/api/mpesa/callback/{INVOICE_NAME}", json=cb)

    assert rp_mock.await_count == 2


def test_callback_record_payment_error_still_returns_200():
    """Errors in record_payment must not return non-200 to Daraja."""
    rp_patch, rp_mock = _mock_record_payment()
    rp_mock.side_effect = Exception("ERPNext unavailable")
    db_patch = _mock_db()

    with rp_patch, db_patch, TestClient(app) as client:
        resp = client.post(
            f"/api/mpesa/callback/{INVOICE_NAME}",
            json=_success_callback(),
        )

    assert resp.status_code == 200
    assert resp.json()["ResultCode"] == 0


def test_callback_result_code_other_than_0_and_1032():
    """Any non-zero result code is a payment failure; no record created."""
    rp_patch, rp_mock = _mock_record_payment()
    db_patch = _mock_db()

    with rp_patch, db_patch, TestClient(app) as client:
        resp = client.post(
            f"/api/mpesa/callback/{INVOICE_NAME}",
            json=_failed_callback(result_code=1037, result_desc="DS timeout"),
        )

    assert resp.status_code == 200
    rp_mock.assert_not_awaited()
