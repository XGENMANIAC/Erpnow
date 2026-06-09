"""
Tests for app/erp/sales.py

Covers:
  - create_sales_order: items POSTed correctly, totals come from ERPNext
  - create_sales_order: document is submitted after creation
  - submit_sales_invoice: invoice created from SO and submitted
  - idempotency key sent as X-Idempotency-Key header
  - ERPNextDuplicateError handled gracefully (re-raised)
  - create_quotation: draft quotation returned with summary_text
"""
from __future__ import annotations

import json as _json
import re
from decimal import Decimal

import httpx
import pytest
import respx

from app.erp.client import ERPNextClient, ERPNextDuplicateError
from app.erp.sales import create_quotation, create_sales_order, submit_sales_invoice
from app.erp.schemas import ERPSalesOrder, OrderItem
from tests.conftest import (
    BASE_URL,
    API_KEY,
    API_SECRET,
    CUSTOMER_NAME,
    SALES_ORDER_DRAFT,
    SALES_ORDER_SUBMITTED,
    SALES_INVOICE_DRAFT,
    SALES_INVOICE_SUBMITTED,
    QUOTATION_DATA,
)


def make_client() -> ERPNextClient:
    return ERPNextClient(base_url=BASE_URL, api_key=API_KEY, api_secret=API_SECRET)


SAMPLE_ITEMS = [OrderItem(item_code="ITEM-001", qty=Decimal("2"))]


# ── create_sales_order ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_sales_order_posts_items():
    """POST body must include items; totals must NOT be computed here."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/api/resource/Sales Order").mock(
            return_value=httpx.Response(200, json={"data": SALES_ORDER_DRAFT})
        )
        mock.put(re.compile(r".*/Sales.{0,3}Order/.*")).mock(
            return_value=httpx.Response(200, json={"data": SALES_ORDER_SUBMITTED})
        )
        client = make_client()
        result = await create_sales_order(
            client, CUSTOMER_NAME, SAMPLE_ITEMS, idempotency_key="so-idem-001"
        )

        # Must verify calls inside the with block
        post_request = next(c for c in mock.calls if c.request.method == "POST")
        body = _json.loads(post_request.request.content)

        # Items must be present
        assert "items" in body
        assert body["items"][0]["item_code"] == "ITEM-001"
        assert body["items"][0]["qty"] == "2"

    # Grand total must come from ERPNext (SALES_ORDER_SUBMITTED), not computed here
    order = result["order"]
    assert order.grand_total == Decimal("3000.00")


@pytest.mark.asyncio
async def test_create_sales_order_submits_after_create():
    """After creating draft, a PUT is issued to submit (docstatus=1)."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/api/resource/Sales Order").mock(
            return_value=httpx.Response(200, json={"data": SALES_ORDER_DRAFT})
        )
        put_mock = mock.put(
            re.compile(r".*/Sales.{0,3}Order/SO-0001.*")
        ).mock(return_value=httpx.Response(200, json={"data": SALES_ORDER_SUBMITTED}))

        client = make_client()
        result = await create_sales_order(
            client, CUSTOMER_NAME, SAMPLE_ITEMS, idempotency_key="so-submit-test"
        )

        # PUT must have been called with docstatus=1
        assert put_mock.called
        put_body = _json.loads(put_mock.calls.last.request.content)
        assert put_body["docstatus"] == 1

    # Returned order must be the submitted one
    assert result["order"].docstatus == 1


@pytest.mark.asyncio
async def test_create_sales_order_idempotency_key_on_post():
    """Idempotency key must be sent as X-Idempotency-Key on the POST."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/api/resource/Sales Order").mock(
            return_value=httpx.Response(200, json={"data": SALES_ORDER_DRAFT})
        )
        mock.put(re.compile(r".*/Sales.{0,3}Order/.*")).mock(
            return_value=httpx.Response(200, json={"data": SALES_ORDER_SUBMITTED})
        )
        client = make_client()
        await create_sales_order(
            client, CUSTOMER_NAME, SAMPLE_ITEMS, idempotency_key="unique-idem-xyz"
        )

        # Must be inside with block
        post_req = next(c for c in mock.calls if c.request.method == "POST")
        assert post_req.request.headers.get("x-idempotency-key") == "unique-idem-xyz"


@pytest.mark.asyncio
async def test_create_sales_order_total_breakdown_from_erpnext():
    """total_breakdown values must come from ERPNext, not be computed here."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/api/resource/Sales Order").mock(
            return_value=httpx.Response(200, json={"data": SALES_ORDER_DRAFT})
        )
        mock.put(re.compile(r".*/Sales.{0,3}Order/.*")).mock(
            return_value=httpx.Response(200, json={"data": SALES_ORDER_SUBMITTED})
        )
        client = make_client()
        result = await create_sales_order(
            client, CUSTOMER_NAME, SAMPLE_ITEMS, idempotency_key="breakdown-test"
        )

    breakdown = result["total_breakdown"]
    assert breakdown["grand_total"] == Decimal("3000.00")
    assert breakdown["taxes_and_charges"] == Decimal("480.00")
    assert breakdown["currency"] == "KES"


@pytest.mark.asyncio
async def test_create_sales_order_duplicate_error_propagated():
    """ERPNextDuplicateError from POST is re-raised to caller."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/api/resource/Sales Order").mock(
            return_value=httpx.Response(
                400,
                json={"exc_type": "DuplicateEntryError", "message": "Already exists"},
            )
        )
        client = make_client()
        with pytest.raises(ERPNextDuplicateError):
            await create_sales_order(
                client, CUSTOMER_NAME, SAMPLE_ITEMS, idempotency_key="dup-key"
            )


# ── submit_sales_invoice ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_sales_invoice_creates_and_submits():
    """Invoice is created from SO, saved, and submitted in sequence."""
    with respx.mock(base_url=BASE_URL) as mock:
        # make_sales_invoice server method
        mock.post(
            f"{BASE_URL}/api/method/erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice"
        ).mock(return_value=httpx.Response(200, json={"data": SALES_INVOICE_DRAFT}))
        # Save invoice
        mock.post(f"{BASE_URL}/api/resource/Sales Invoice").mock(
            return_value=httpx.Response(200, json={"data": SALES_INVOICE_DRAFT})
        )
        # Submit invoice (PUT)
        mock.put(
            re.compile(r".*/Sales.{0,3}Invoice/SINV-0001.*")
        ).mock(return_value=httpx.Response(200, json={"data": SALES_INVOICE_SUBMITTED}))

        client = make_client()
        result = await submit_sales_invoice(client, "SO-0001", idempotency_key="inv-idem-001")

    assert result["invoice_no"] == "SINV-0001"
    assert "SINV-0001" in result["pdf_url"]
    assert "KES 3000.00" in result["summary_text"]


@pytest.mark.asyncio
async def test_submit_invoice_idempotency_key_forwarded():
    """Idempotency key is forwarded to the make_sales_invoice POST."""
    with respx.mock(base_url=BASE_URL) as mock:
        method_mock = mock.post(
            f"{BASE_URL}/api/method/erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice"
        ).mock(return_value=httpx.Response(200, json={"data": SALES_INVOICE_DRAFT}))
        mock.post(f"{BASE_URL}/api/resource/Sales Invoice").mock(
            return_value=httpx.Response(200, json={"data": SALES_INVOICE_DRAFT})
        )
        mock.put(re.compile(r".*/Sales.{0,3}Invoice/.*")).mock(
            return_value=httpx.Response(200, json={"data": SALES_INVOICE_SUBMITTED})
        )

        client = make_client()
        await submit_sales_invoice(client, "SO-0001", idempotency_key="inv-key-abc")

        assert method_mock.calls.last.request.headers.get("x-idempotency-key") == "inv-key-abc"


# ── create_quotation ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_quotation_returns_summary():
    """create_quotation returns dict with quotation, total, and summary_text."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post(f"{BASE_URL}/api/resource/Quotation").mock(
            return_value=httpx.Response(200, json={"data": QUOTATION_DATA})
        )
        client = make_client()
        result = await create_quotation(
            client, CUSTOMER_NAME, SAMPLE_ITEMS, idempotency_key="qtn-idem-001"
        )

    assert result["total"] == Decimal("1500.00")
    assert "QTN-0001" in result["summary_text"]
    assert "quotation" in result


@pytest.mark.asyncio
async def test_create_quotation_stays_draft():
    """Quotation must NOT be submitted (no PUT to submit after creation)."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post(f"{BASE_URL}/api/resource/Quotation").mock(
            return_value=httpx.Response(200, json={"data": QUOTATION_DATA})
        )
        client = make_client()
        await create_quotation(
            client, CUSTOMER_NAME, SAMPLE_ITEMS, idempotency_key="qtn-draft"
        )

    # No PUT calls should have been made
    put_calls = [c for c in mock.calls if c.request.method == "PUT"]
    assert len(put_calls) == 0
