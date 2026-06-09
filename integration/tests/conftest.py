"""
pytest fixtures and mock ERPNext server.

All tests use `respx` to intercept httpx calls — no live ERPNext needed.
The MockERPNextServer class centralises URL patterns and canned responses.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from app.erp.client import ERPNextClient

# ── Constants used across tests ───────────────────────────────────────────────

BASE_URL = "http://erpnext.test"
API_KEY = "testkey"
API_SECRET = "testsecret"

CUSTOMER_PHONE = "+254712345678"
CUSTOMER_NAME = "CUST-0001"
CUSTOMER_DATA = {
    "name": CUSTOMER_NAME,
    "customer_name": "Test Customer",
    "mobile_no": CUSTOMER_PHONE,
    "custom_phone": CUSTOMER_PHONE,
    "customer_type": "Individual",
    "customer_group": "All Customer Groups",
    "territory": "Kenya",
}

ITEM_DATA = {
    "name": "ITEM-001",
    "item_name": "Test Widget",
    "item_code": "ITEM-001",
    "description": "A test widget",
    "stock_uom": "Nos",
    "is_stock_item": True,
}

ITEM_PRICE_DATA = {
    "item_code": "ITEM-001",
    "price_list": "Standard Selling",
    "currency": "KES",
    "price_list_rate": "1500.00",
}

STOCK_BIN_DATA = {
    "item_code": "ITEM-001",
    "warehouse": "Stores - MC",
    "actual_qty": "50.00",
    "reserved_qty": "5.00",
    "projected_qty": "45.00",
}

SALES_ORDER_DRAFT = {
    "name": "SO-0001",
    "customer": CUSTOMER_NAME,
    "items": [{"item_code": "ITEM-001", "qty": "2", "rate": "1500", "amount": "3000"}],
    "grand_total": "3000.00",
    "total_taxes_and_charges": "480.00",
    "status": "Draft",
    "docstatus": 0,
}

SALES_ORDER_SUBMITTED = {
    **SALES_ORDER_DRAFT,
    "status": "To Deliver and Bill",
    "docstatus": 1,
}

SALES_INVOICE_DRAFT = {
    "name": "SINV-0001",
    "customer": CUSTOMER_NAME,
    "sales_order": "SO-0001",
    "grand_total": "3000.00",
    "outstanding_amount": "3000.00",
    "status": "Draft",
    "docstatus": 0,
}

SALES_INVOICE_SUBMITTED = {
    **SALES_INVOICE_DRAFT,
    "status": "Unpaid",
    "docstatus": 1,
}

QUOTATION_DATA = {
    "name": "QTN-0001",
    "party_name": CUSTOMER_NAME,
    "customer": CUSTOMER_NAME,
    "items": [{"item_code": "ITEM-001", "qty": "1", "rate": "1500", "amount": "1500"}],
    "grand_total": "1500.00",
    "docstatus": 0,
}


# ── Helper: wrap data in ERPNext "data" envelope ──────────────────────────────

def _erp_ok(data: Any) -> httpx.Response:
    """Return a 200 response with ERPNext's {"data": ...} envelope."""
    return httpx.Response(200, json={"data": data})


def _erp_list(rows: list) -> httpx.Response:
    """Return a 200 response with a list (ERPNext list endpoint format)."""
    return httpx.Response(200, json={"data": rows})


def _erp_error(status: int, exc_type: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={"exc_type": exc_type, "message": message})


# ── Shared client fixture ─────────────────────────────────────────────────────

@pytest.fixture
def erp_client() -> ERPNextClient:
    """Return an ERPNextClient pointed at the test base URL."""
    return ERPNextClient(
        base_url=BASE_URL,
        api_key=API_KEY,
        api_secret=API_SECRET,
    )


# ── Customer fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_customer_exists(respx_mock):
    """Customer exists: search returns one result."""
    # custom_phone search returns customer
    respx_mock.get(
        f"{BASE_URL}/api/resource/Customer",
    ).mock(return_value=_erp_list([CUSTOMER_DATA]))
    return CUSTOMER_DATA


@pytest.fixture
def mock_customer_not_found(respx_mock):
    """Customer does not exist: all searches return empty, create succeeds."""
    # Both custom_phone and mobile_no searches return empty
    respx_mock.get(
        f"{BASE_URL}/api/resource/Customer",
    ).mock(return_value=_erp_list([]))
    # Create call returns new customer
    respx_mock.post(
        f"{BASE_URL}/api/resource/Customer",
    ).mock(return_value=_erp_ok(CUSTOMER_DATA))
    return CUSTOMER_DATA


# ── Catalog fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def mock_items(respx_mock):
    """Items list returns one item."""
    respx_mock.get(f"{BASE_URL}/api/resource/Item").mock(
        return_value=_erp_list([ITEM_DATA])
    )
    return [ITEM_DATA]


@pytest.fixture
def mock_item_price(respx_mock):
    """Item Price returns one price row."""
    respx_mock.get(f"{BASE_URL}/api/resource/Item Price").mock(
        return_value=_erp_list([ITEM_PRICE_DATA])
    )
    # Also mock the frappe.client.get_value fallback
    respx_mock.post(
        f"{BASE_URL}/api/method/frappe.client.get_value"
    ).mock(
        return_value=_erp_ok(
            {"price_list_rate": "1500.00", "currency": "KES"}
        )
    )
    return ITEM_PRICE_DATA


@pytest.fixture
def mock_stock_bin(respx_mock):
    """Bin returns one stock record."""
    respx_mock.get(f"{BASE_URL}/api/resource/Bin").mock(
        return_value=_erp_list([STOCK_BIN_DATA])
    )
    return STOCK_BIN_DATA


# ── Sales fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def mock_create_sales_order(respx_mock):
    """SO creation returns draft; PUT submission returns submitted."""
    respx_mock.post(f"{BASE_URL}/api/resource/Sales Order").mock(
        return_value=_erp_ok(SALES_ORDER_DRAFT)
    )
    respx_mock.put(re.compile(rf"{re.escape(BASE_URL)}/api/resource/Sales Order/.*")).mock(
        return_value=_erp_ok(SALES_ORDER_SUBMITTED)
    )
    return SALES_ORDER_SUBMITTED


@pytest.fixture
def mock_create_invoice(respx_mock):
    """Invoice creation via method returns draft body; save and submit succeed."""
    respx_mock.post(
        f"{BASE_URL}/api/method/erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice"
    ).mock(return_value=_erp_ok(SALES_INVOICE_DRAFT))
    respx_mock.post(f"{BASE_URL}/api/resource/Sales Invoice").mock(
        return_value=_erp_ok(SALES_INVOICE_DRAFT)
    )
    respx_mock.put(
        re.compile(rf"{re.escape(BASE_URL)}/api/resource/Sales Invoice/.*")
    ).mock(return_value=_erp_ok(SALES_INVOICE_SUBMITTED))
    return SALES_INVOICE_SUBMITTED


@pytest.fixture
def mock_create_quotation(respx_mock):
    """Quotation creation succeeds."""
    respx_mock.post(f"{BASE_URL}/api/resource/Quotation").mock(
        return_value=_erp_ok(QUOTATION_DATA)
    )
    return QUOTATION_DATA
