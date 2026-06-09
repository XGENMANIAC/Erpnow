"""
Tests for app/erp/customers.py

Covers:
  - find_or_create_customer: existing customer found by phone
  - find_or_create_customer: customer not found, creates new one
  - idempotency: calling twice for the same new customer returns same record
  - phone normalization: 07xx, 254xx, +254xx forms all work
"""
from __future__ import annotations

import re

import httpx
import pytest
import respx

from app.erp.customers import _normalize_phone, find_or_create_customer
from app.erp.schemas import ERPCustomer
from tests.conftest import (
    BASE_URL,
    API_KEY,
    API_SECRET,
    CUSTOMER_DATA,
    CUSTOMER_PHONE,
    CUSTOMER_NAME,
)
from app.erp.client import ERPNextClient


def make_client() -> ERPNextClient:
    return ERPNextClient(base_url=BASE_URL, api_key=API_KEY, api_secret=API_SECRET)


# ── Phone normalization ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("0712345678", "+254712345678"),
        ("254712345678", "+254712345678"),
        ("+254712345678", "+254712345678"),
        ("  0712345678  ", "+254712345678"),
    ],
)
def test_normalize_phone(raw: str, expected: str):
    assert _normalize_phone(raw) == expected


# ── Find existing customer ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_existing_customer_by_custom_phone():
    """When customer exists, return it without creating a new one."""
    with respx.mock(base_url=BASE_URL) as mock:
        # First search (custom_phone) returns a match
        mock.get("/api/resource/Customer").mock(
            return_value=httpx.Response(200, json={"data": [CUSTOMER_DATA]})
        )
        client = make_client()
        customer = await find_or_create_customer(client, CUSTOMER_PHONE)

    assert isinstance(customer, ERPCustomer)
    assert customer.name == CUSTOMER_NAME
    assert customer.custom_phone == CUSTOMER_PHONE
    # Must not have called POST
    assert not mock.calls.call_count or all(
        c.request.method == "GET" for c in mock.calls
    )


@pytest.mark.asyncio
async def test_find_existing_customer_by_mobile_no():
    """Fallback to mobile_no search when custom_phone search returns empty."""
    with respx.mock(base_url=BASE_URL) as mock:
        call_count = [0]

        def handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            if call_count[0] == 1:
                # First search (custom_phone) — empty
                return httpx.Response(200, json={"data": []})
            # Second search (mobile_no) — found
            return httpx.Response(200, json={"data": [CUSTOMER_DATA]})

        mock.get("/api/resource/Customer").mock(side_effect=handler)
        client = make_client()
        customer = await find_or_create_customer(client, CUSTOMER_PHONE)

    assert customer.name == CUSTOMER_NAME
    assert call_count[0] == 2


# ── Create new customer ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_new_customer_when_not_found():
    """When no customer exists, POST a new one and return the result."""
    import json as _json

    with respx.mock(base_url=BASE_URL) as mock:
        # Both searches return empty
        mock.get("/api/resource/Customer").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        created = {
            **CUSTOMER_DATA,
            "customer_name": "Alice",
            "name": "CUST-0042",
        }
        mock.post("/api/resource/Customer").mock(
            return_value=httpx.Response(200, json={"data": created})
        )
        client = make_client()
        customer = await find_or_create_customer(
            client, CUSTOMER_PHONE, name="Alice", idempotency_key="idem-alice-001"
        )

        assert customer.name == "CUST-0042"
        assert customer.customer_name == "Alice"

        # Verify POST payload contains expected fields — must be inside with block
        post_request = next(c for c in mock.calls if c.request.method == "POST")
        body = _json.loads(post_request.request.content)
        assert body["customer_name"] == "Alice"
        assert body["territory"] == "Kenya"
        assert body["customer_type"] == "Individual"
        assert body["mobile_no"] == CUSTOMER_PHONE


@pytest.mark.asyncio
async def test_create_customer_sets_idempotency_header():
    """The idempotency_key must be sent as X-Idempotency-Key header on POST."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/resource/Customer").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        mock.post("/api/resource/Customer").mock(
            return_value=httpx.Response(200, json={"data": CUSTOMER_DATA})
        )
        client = make_client()
        await find_or_create_customer(
            client, CUSTOMER_PHONE, idempotency_key="idem-test-999"
        )

        # Must be checked inside the with block
        post_request = next(c for c in mock.calls if c.request.method == "POST")
        assert post_request.request.headers.get("x-idempotency-key") == "idem-test-999"


# ── Idempotency ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_idempotency_second_call_returns_same_customer():
    """
    Calling find_or_create_customer twice for the same phone returns the same
    customer.  The second call finds the customer that was created in the first.
    """
    with respx.mock(base_url=BASE_URL) as mock:
        first_call = [True]

        def get_handler(request: httpx.Request) -> httpx.Response:
            if first_call[0]:
                return httpx.Response(200, json={"data": []})
            return httpx.Response(200, json={"data": [CUSTOMER_DATA]})

        mock.get("/api/resource/Customer").mock(side_effect=get_handler)
        mock.post("/api/resource/Customer").mock(
            return_value=httpx.Response(200, json={"data": CUSTOMER_DATA})
        )

        client = make_client()

        # First call: creates the customer
        c1 = await find_or_create_customer(client, CUSTOMER_PHONE, idempotency_key="idem-1")
        assert c1.name == CUSTOMER_NAME

        # Simulate that on the next GET the customer is found
        first_call[0] = False

        # Second call: finds the existing customer
        c2 = await find_or_create_customer(client, CUSTOMER_PHONE, idempotency_key="idem-1")
        assert c2.name == CUSTOMER_NAME


# ── Phone normalisation in storage ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_phone_normalised_before_create():
    """Raw phone (07xx) is normalised to +254xx before being stored."""
    import json as _json

    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/resource/Customer").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        mock.post("/api/resource/Customer").mock(
            return_value=httpx.Response(200, json={"data": CUSTOMER_DATA})
        )
        client = make_client()
        await find_or_create_customer(client, "0712345678")

        # Must be checked inside the with block
        post_req = next(c for c in mock.calls if c.request.method == "POST")
        body = _json.loads(post_req.request.content)
        assert body["mobile_no"] == "+254712345678"
        assert body["custom_phone"] == "+254712345678"
