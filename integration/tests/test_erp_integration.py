"""
Integration tests for the full ERP order flow.

All ERPNext calls are mocked with respx; no live instance required.

Flow under test:
  1. find_or_create_customer (by phone)
  2. get_price (fetch item price)
  3. check_stock (verify stock)
  4. create_sales_order (create + submit SO)
  5. submit_sales_invoice (create + submit invoice)

Key assertions:
  - ERPNext is the sole source of all totals (no local computation).
  - All mutating steps carry idempotency keys.
  - The customer from step 1 flows into steps 4 and 5.
  - The sales order from step 4 flows into step 5.
"""
from __future__ import annotations

import re
from decimal import Decimal

import httpx
import pytest
import respx

from app.erp.catalog import check_stock, get_price
from app.erp.client import ERPNextClient
from app.erp.customers import find_or_create_customer
from app.erp.sales import create_sales_order, submit_sales_invoice
from app.erp.schemas import OrderItem
from tests.conftest import (
    BASE_URL,
    API_KEY,
    API_SECRET,
    CUSTOMER_DATA,
    CUSTOMER_NAME,
    CUSTOMER_PHONE,
    ITEM_PRICE_DATA,
    SALES_ORDER_DRAFT,
    SALES_ORDER_SUBMITTED,
    SALES_INVOICE_DRAFT,
    SALES_INVOICE_SUBMITTED,
    STOCK_BIN_DATA,
)


def make_client() -> ERPNextClient:
    return ERPNextClient(base_url=BASE_URL, api_key=API_KEY, api_secret=API_SECRET)


# ── Full order flow ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_order_flow():
    """
    End-to-end: customer lookup → price check → stock check
    → create SO → submit invoice.

    Asserts:
      - Each step uses results from the previous step (no hardcoding).
      - All totals originate from ERPNext mock data.
      - Idempotency keys are present on mutating calls.
    """
    with respx.mock(base_url=BASE_URL) as mock:
        # Step 1: find_or_create_customer
        mock.get("/api/resource/Customer").mock(
            return_value=httpx.Response(200, json={"data": [CUSTOMER_DATA]})
        )

        # Step 2: get_price — Item Price direct lookup
        mock.get("/api/resource/Item Price").mock(
            return_value=httpx.Response(200, json={"data": [ITEM_PRICE_DATA]})
        )

        # Step 3: check_stock
        mock.get("/api/resource/Bin").mock(
            return_value=httpx.Response(200, json={"data": [STOCK_BIN_DATA]})
        )

        # Step 4: create_sales_order (POST = draft, PUT = submitted)
        mock.post("/api/resource/Sales Order").mock(
            return_value=httpx.Response(200, json={"data": SALES_ORDER_DRAFT})
        )
        mock.put(
            re.compile(rf"{re.escape(BASE_URL)}/api/resource/Sales.{{0,3}}Order/.*")
        ).mock(return_value=httpx.Response(200, json={"data": SALES_ORDER_SUBMITTED}))

        # Step 5: submit_sales_invoice
        mock.post(
            f"{BASE_URL}/api/method/erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice"
        ).mock(return_value=httpx.Response(200, json={"data": SALES_INVOICE_DRAFT}))
        mock.post(f"{BASE_URL}/api/resource/Sales Invoice").mock(
            return_value=httpx.Response(200, json={"data": SALES_INVOICE_DRAFT})
        )
        mock.put(
            re.compile(rf"{re.escape(BASE_URL)}/api/resource/Sales.{{0,3}}Invoice/.*")
        ).mock(return_value=httpx.Response(200, json={"data": SALES_INVOICE_SUBMITTED}))

        client = make_client()

        # ── Step 1: Customer ─────────────────────────────────────────────────
        customer = await find_or_create_customer(
            client, CUSTOMER_PHONE, idempotency_key="flow-customer-001"
        )
        assert customer.name == CUSTOMER_NAME

        # ── Step 2: Price ────────────────────────────────────────────────────
        price = await get_price(client, "ITEM-001", "Standard Selling")
        assert isinstance(price.price_list_rate, Decimal)
        assert price.price_list_rate == Decimal("1500.00")

        # ── Step 3: Stock ────────────────────────────────────────────────────
        stock = await check_stock(client, "ITEM-001", warehouse="Stores - MC")
        assert stock.actual_qty == Decimal("50.00")
        assert stock.actual_qty > Decimal("0")  # In stock

        # ── Step 4: Sales Order ───────────────────────────────────────────────
        items = [OrderItem(item_code="ITEM-001", qty=Decimal("2"))]
        order_result = await create_sales_order(
            client,
            customer.name,            # from step 1
            items,
            idempotency_key="flow-so-001",
        )
        order = order_result["order"]
        assert order.docstatus == 1
        assert order.customer == CUSTOMER_NAME
        # Total comes from ERPNext mock, NOT computed here
        assert order.grand_total == Decimal("3000.00")

        # ── Step 5: Invoice ──────────────────────────────────────────────────
        invoice_result = await submit_sales_invoice(
            client,
            order.name,               # from step 4
            idempotency_key="flow-inv-001",
        )
        assert invoice_result["invoice_no"] == "SINV-0001"
        assert "SINV-0001" in invoice_result["pdf_url"]

        # Verify the SO was created for the correct customer — inside with block
        import json as _json
        so_post = next(
            c for c in mock.calls
            if c.request.method == "POST"
            and "Sales" in str(c.request.url)
            and "Order" in str(c.request.url)
            and "method" not in str(c.request.url)
        )
        so_body = _json.loads(so_post.request.content)
        assert so_body["customer"] == CUSTOMER_NAME


@pytest.mark.asyncio
async def test_full_flow_idempotency_keys_on_all_mutations():
    """All mutating calls (POST/PUT) in the flow must carry idempotency keys."""
    with respx.mock(base_url=BASE_URL) as mock:
        # Customer already exists
        mock.get("/api/resource/Customer").mock(
            return_value=httpx.Response(200, json={"data": [CUSTOMER_DATA]})
        )
        mock.get("/api/resource/Item Price").mock(
            return_value=httpx.Response(200, json={"data": [ITEM_PRICE_DATA]})
        )
        mock.get("/api/resource/Bin").mock(
            return_value=httpx.Response(200, json={"data": [STOCK_BIN_DATA]})
        )
        mock.post("/api/resource/Sales Order").mock(
            return_value=httpx.Response(200, json={"data": SALES_ORDER_DRAFT})
        )
        mock.put(
            re.compile(rf"{re.escape(BASE_URL)}/api/resource/Sales.{{0,3}}Order/.*")
        ).mock(return_value=httpx.Response(200, json={"data": SALES_ORDER_SUBMITTED}))
        mock.post(
            f"{BASE_URL}/api/method/erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice"
        ).mock(return_value=httpx.Response(200, json={"data": SALES_INVOICE_DRAFT}))
        mock.post(f"{BASE_URL}/api/resource/Sales Invoice").mock(
            return_value=httpx.Response(200, json={"data": SALES_INVOICE_DRAFT})
        )
        mock.put(
            re.compile(rf"{re.escape(BASE_URL)}/api/resource/Sales.{{0,3}}Invoice/.*")
        ).mock(return_value=httpx.Response(200, json={"data": SALES_INVOICE_SUBMITTED}))

        client = make_client()

        customer = await find_or_create_customer(client, CUSTOMER_PHONE)
        await get_price(client, "ITEM-001", "Standard Selling")
        await check_stock(client, "ITEM-001")
        items = [OrderItem(item_code="ITEM-001", qty=Decimal("1"))]
        order_result = await create_sales_order(
            client, customer.name, items, idempotency_key="idem-so-flow"
        )
        await submit_sales_invoice(
            client, order_result["order"].name, idempotency_key="idem-inv-flow"
        )

        # Every POST that creates/mutates a document must have idempotency header
        # (checked inside with block so mock.calls is accessible)
        mutating_posts = [
            c for c in mock.calls
            if c.request.method in ("POST", "PUT")
            and "method/frappe" not in str(c.request.url)
            and "api/resource" in str(c.request.url)
        ]
        for call in mutating_posts:
            assert "x-idempotency-key" in call.request.headers, (
                f"Missing X-Idempotency-Key on {call.request.method} {call.request.url}"
            )


@pytest.mark.asyncio
async def test_totals_always_from_erpnext_not_computed():
    """
    Verify that at no point does the service compute monetary totals.
    The grand_total in the result must match exactly what ERPNext returned.
    """
    # ERPNext returns 3480 (3000 net + 480 VAT)
    erp_total = Decimal("3480.00")
    so_with_vat = {**SALES_ORDER_SUBMITTED, "grand_total": str(erp_total), "total_taxes_and_charges": "480.00"}

    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/resource/Customer").mock(
            return_value=httpx.Response(200, json={"data": [CUSTOMER_DATA]})
        )
        mock.post("/api/resource/Sales Order").mock(
            return_value=httpx.Response(200, json={"data": SALES_ORDER_DRAFT})
        )
        mock.put(
            re.compile(rf"{re.escape(BASE_URL)}/api/resource/Sales.{{0,3}}Order/.*")
        ).mock(return_value=httpx.Response(200, json={"data": so_with_vat}))

        client = make_client()
        customer = await find_or_create_customer(client, CUSTOMER_PHONE)
        items = [OrderItem(item_code="ITEM-001", qty=Decimal("2"))]
        result = await create_sales_order(
            client, customer.name, items, idempotency_key="total-test"
        )

    assert result["order"].grand_total == erp_total, (
        "grand_total must be the value ERPNext returned, not recomputed"
    )
