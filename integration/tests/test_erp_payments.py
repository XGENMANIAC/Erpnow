"""
Unit tests for app.erp.payments — request_mpesa_payment and record_payment.

ERPNext calls are mocked with respx.
DarajaClient is patched with unittest.mock so no real Daraja credentials needed.
"""
from __future__ import annotations

import re
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from app.erp.client import ERPNextClient, ERPNextDuplicateError
from app.erp.payments import record_payment, request_mpesa_payment
from app.erp.schemas import ERPPaymentEntry

BASE_URL = "http://erpnext.test"
INVOICE_NAME = "ACC-SINV-2026-00001"
CUSTOMER = "CUST-0001"
MPESA_REF = "NLJ7RT61SV"
AMOUNT = Decimal("406.00")

INVOICE_DATA = {
    "name": INVOICE_NAME,
    "customer": CUSTOMER,
    "company": "DEWMIX Hardware",
    "grand_total": "406.00",
    "outstanding_amount": "406.00",
    "status": "Unpaid",
    "docstatus": 1,
}

PAYMENT_ENTRY_DRAFT = {
    "name": "PE-0001",
    "party": CUSTOMER,
    "paid_amount": "406.00",
    "reference_no": MPESA_REF,
    "docstatus": 0,
}

PAYMENT_ENTRY_SUBMITTED = {
    **PAYMENT_ENTRY_DRAFT,
    "docstatus": 1,
}


def _erp_ok(data) -> httpx.Response:
    return httpx.Response(200, json={"data": data})


def _erp_list(rows: list) -> httpx.Response:
    return httpx.Response(200, json={"data": rows})


@pytest.fixture
def erp_client() -> ERPNextClient:
    return ERPNextClient(base_url=BASE_URL, api_key="key", api_secret="secret")


# ── request_mpesa_payment ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_request_mpesa_payment_returns_pending(erp_client):
    """Happy path: STK push succeeds → returns pending dict."""
    mock_stk_result = {
        "CheckoutRequestID": "ws_CO_test_123",
        "MerchantRequestID": "MR_test_456",
        "ResponseCode": "0",
        "CustomerMessage": "Please enter your PIN.",
    }

    mock_daraja = MagicMock()
    mock_daraja.__aenter__ = AsyncMock(return_value=mock_daraja)
    mock_daraja.__aexit__ = AsyncMock(return_value=None)
    mock_daraja.stk_push = AsyncMock(return_value=mock_stk_result)

    with patch("app.erp.payments._make_daraja_client", return_value=mock_daraja):
        result = await request_mpesa_payment(
            client=erp_client,
            invoice_name=INVOICE_NAME,
            phone="+254712345678",
            amount=AMOUNT,
            idempotency_key="pay:test:001",
        )

    assert result["status"] == "pending"
    assert result["checkout_request_id"] == "ws_CO_test_123"
    assert result["merchant_request_id"] == "MR_test_456"
    assert result["idempotency_key"] == "pay:test:001"
    assert "PIN" in result["message"] or "Please" in result["message"]


@pytest.mark.asyncio
async def test_request_mpesa_payment_passes_per_invoice_callback(erp_client):
    """The callback URL passed to Daraja should include the invoice name."""
    captured_kwargs = {}

    async def capture_stk(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "CheckoutRequestID": "ws_CO_001",
            "MerchantRequestID": "MR_001",
            "ResponseCode": "0",
            "CustomerMessage": "OK",
        }

    mock_daraja = MagicMock()
    mock_daraja.__aenter__ = AsyncMock(return_value=mock_daraja)
    mock_daraja.__aexit__ = AsyncMock(return_value=None)
    mock_daraja.stk_push = AsyncMock(side_effect=capture_stk)

    with patch("app.erp.payments._make_daraja_client", return_value=mock_daraja):
        await request_mpesa_payment(
            client=erp_client,
            invoice_name=INVOICE_NAME,
            phone="+254712345678",
            amount=AMOUNT,
            idempotency_key="pay:test:001",
        )

    callback_url = captured_kwargs.get("callback_url", "")
    assert INVOICE_NAME in callback_url


@pytest.mark.asyncio
async def test_request_mpesa_payment_propagates_daraja_error(erp_client):
    """DarajaError should propagate to the caller."""
    from app.mpesa.client import DarajaError

    mock_daraja = MagicMock()
    mock_daraja.__aenter__ = AsyncMock(return_value=mock_daraja)
    mock_daraja.__aexit__ = AsyncMock(return_value=None)
    mock_daraja.stk_push = AsyncMock(side_effect=DarajaError("Invalid phone number"))

    with patch("app.erp.payments._make_daraja_client", return_value=mock_daraja):
        with pytest.raises(DarajaError, match="Invalid phone number"):
            await request_mpesa_payment(
                client=erp_client,
                invoice_name=INVOICE_NAME,
                phone="invalid",
                amount=AMOUNT,
                idempotency_key="pay:test:fail",
            )


# ── record_payment ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_payment_creates_and_submits(erp_client):
    """Happy path: no existing entry → POST draft → PUT submit."""
    with respx.mock(assert_all_called=False) as mock:
        # No existing entry
        mock.get(re.compile(rf"{re.escape(BASE_URL)}/api/resource/Payment.{{0,3}}Entry(?:\?|$)")).mock(
            return_value=_erp_list([])
        )
        # Invoice lookup
        mock.get(re.compile(rf"{re.escape(BASE_URL)}/api/resource/Sales.{{0,3}}Invoice/.*")).mock(
            return_value=_erp_ok(INVOICE_DATA)
        )
        # Create draft
        mock.post(re.compile(rf"{re.escape(BASE_URL)}/api/resource/Payment.{{0,3}}Entry")).mock(
            return_value=_erp_ok(PAYMENT_ENTRY_DRAFT)
        )
        # Submit
        mock.put(
            re.compile(rf"{re.escape(BASE_URL)}/api/resource/Payment.{{0,3}}Entry/.*")
        ).mock(return_value=_erp_ok(PAYMENT_ENTRY_SUBMITTED))

        entry = await record_payment(
            client=erp_client,
            invoice_name=INVOICE_NAME,
            mpesa_ref=MPESA_REF,
            amount=AMOUNT,
            idempotency_key="mpesa:ws_CO_001",
        )

    assert entry.name == "PE-0001"
    assert entry.reference_no == MPESA_REF
    assert entry.docstatus == 1
    assert entry.paid_amount == AMOUNT


@pytest.mark.asyncio
async def test_record_payment_idempotent_existing_entry(erp_client):
    """If a Payment Entry with the same mpesa_ref exists, return it — no duplicate POST."""
    with respx.mock(assert_all_called=False) as mock:
        # Existing entry found
        mock.get(re.compile(rf"{re.escape(BASE_URL)}/api/resource/Payment.{{0,3}}Entry(?:\?|$)")).mock(
            return_value=_erp_list([PAYMENT_ENTRY_SUBMITTED])
        )
        post_mock = mock.post(f"{BASE_URL}/api/resource/Payment Entry").mock(
            return_value=_erp_ok(PAYMENT_ENTRY_DRAFT)
        )

        entry = await record_payment(
            client=erp_client,
            invoice_name=INVOICE_NAME,
            mpesa_ref=MPESA_REF,
            amount=AMOUNT,
            idempotency_key="mpesa:ws_CO_001",
        )

        # POST should NOT have been called
        assert not post_mock.called

    assert entry.reference_no == MPESA_REF
    assert entry.name == "PE-0001"


@pytest.mark.asyncio
async def test_record_payment_idempotent_name_only_row(erp_client):
    """
    ERPNext sometimes returns summary rows (just {name: ...}).
    We fetch the full document in that case.
    """
    with respx.mock(assert_all_called=False) as mock:
        # List returns a summary row
        mock.get(re.compile(rf"{re.escape(BASE_URL)}/api/resource/Payment.{{0,3}}Entry(?:\?|$)")).mock(
            return_value=_erp_list([{"name": "PE-0001"}])
        )
        # Full doc fetch
        mock.get(re.compile(rf"{re.escape(BASE_URL)}/api/resource/Payment.{{0,3}}Entry/.*")).mock(
            return_value=_erp_ok(PAYMENT_ENTRY_SUBMITTED)
        )
        post_mock = mock.post(
            re.compile(rf"{re.escape(BASE_URL)}/api/resource/Payment.{{0,3}}Entry")
        ).mock(return_value=_erp_ok(PAYMENT_ENTRY_DRAFT))

        entry = await record_payment(
            client=erp_client,
            invoice_name=INVOICE_NAME,
            mpesa_ref=MPESA_REF,
            amount=AMOUNT,
            idempotency_key="mpesa:ws_CO_001",
        )

        assert not post_mock.called

    assert entry.name == "PE-0001"


@pytest.mark.asyncio
async def test_record_payment_propagates_duplicate_error(erp_client):
    """ERPNextDuplicateError from ERPNext propagates; not swallowed."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(re.compile(rf"{re.escape(BASE_URL)}/api/resource/Payment.{{0,3}}Entry(?:\?|$)")).mock(
            return_value=_erp_list([])
        )
        mock.get(re.compile(rf"{re.escape(BASE_URL)}/api/resource/Sales.{{0,3}}Invoice/.*")).mock(
            return_value=_erp_ok(INVOICE_DATA)
        )
        mock.post(re.compile(rf"{re.escape(BASE_URL)}/api/resource/Payment.{{0,3}}Entry")).mock(
            return_value=httpx.Response(
                409, json={"exc_type": "DuplicateEntryError", "message": "already exists"}
            )
        )

        with pytest.raises(ERPNextDuplicateError):
            await record_payment(
                client=erp_client,
                invoice_name=INVOICE_NAME,
                mpesa_ref=MPESA_REF,
                amount=AMOUNT,
                idempotency_key="mpesa:ws_CO_001",
            )


@pytest.mark.asyncio
async def test_record_payment_idempotency_key_forwarded(erp_client):
    """The idempotency_key should be forwarded as X-Idempotency-Key header."""
    idem_key = "mpesa:ws_CO_unique_001"
    captured_headers: dict = {}

    def capture_create(request: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(request.headers))
        return httpx.Response(200, json={"data": PAYMENT_ENTRY_DRAFT})

    with respx.mock(assert_all_called=False) as mock:
        mock.get(re.compile(rf"{re.escape(BASE_URL)}/api/resource/Payment.{{0,3}}Entry(?:\?|$)")).mock(
            return_value=_erp_list([])
        )
        mock.get(re.compile(rf"{re.escape(BASE_URL)}/api/resource/Sales.{{0,3}}Invoice/.*")).mock(
            return_value=_erp_ok(INVOICE_DATA)
        )
        mock.post(re.compile(rf"{re.escape(BASE_URL)}/api/resource/Payment.{{0,3}}Entry")).mock(
            side_effect=capture_create
        )
        mock.put(
            re.compile(rf"{re.escape(BASE_URL)}/api/resource/Payment.{{0,3}}Entry/.*")
        ).mock(return_value=_erp_ok(PAYMENT_ENTRY_SUBMITTED))

        await record_payment(
            client=erp_client,
            invoice_name=INVOICE_NAME,
            mpesa_ref=MPESA_REF,
            amount=AMOUNT,
            idempotency_key=idem_key,
        )

    assert captured_headers.get("x-idempotency-key") == idem_key
