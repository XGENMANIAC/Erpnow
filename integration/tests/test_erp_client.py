"""
Tests for app/erp/client.py

Covers:
  - Auth header present on every request
  - Retry on 500 (3 attempts total)
  - No retry on 400 / 404
  - ERPNextNotFoundError on 404
  - ERPNextValidationError on 417 and 422
  - ERPNextAuthError on 401
  - ERPNextDuplicateError on DuplicateEntry exc_type
  - Idempotency header forwarded on POST
  - Successful GET returns unwrapped "data"
"""
from __future__ import annotations

import pytest
import httpx
import respx

from app.erp.client import (
    ERPNextAuthError,
    ERPNextClient,
    ERPNextDuplicateError,
    ERPNextError,
    ERPNextNotFoundError,
    ERPNextValidationError,
)
from tests.conftest import API_KEY, API_SECRET, BASE_URL


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_client() -> ERPNextClient:
    return ERPNextClient(base_url=BASE_URL, api_key=API_KEY, api_secret=API_SECRET)


def erp_error_response(status: int, exc_type: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={"exc_type": exc_type, "message": message})


# ── Auth ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auth_header_present():
    """Authorization: token key:secret header must be on every request."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/resource/Item/ITEM-001").mock(
            return_value=httpx.Response(200, json={"data": {"name": "ITEM-001"}})
        )
        client = make_client()
        await client.get("/api/resource/Item/ITEM-001")
        request = mock.calls.last.request
        auth = request.headers.get("authorization", "")
        assert auth == f"token {API_KEY}:{API_SECRET}"


# ── Idempotency header ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_idempotency_header_forwarded_on_post():
    """POST with idempotency_key must set X-Idempotency-Key header."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/api/resource/Customer").mock(
            return_value=httpx.Response(200, json={"data": {"name": "CUST-001"}})
        )
        client = make_client()
        await client.post(
            "/api/resource/Customer",
            json={"customer_name": "Test"},
            idempotency_key="idem-key-123",
        )
        request = mock.calls.last.request
        assert request.headers.get("x-idempotency-key") == "idem-key-123"


@pytest.mark.asyncio
async def test_no_idempotency_header_when_not_provided():
    """POST without idempotency_key must NOT set X-Idempotency-Key header."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/api/resource/Customer").mock(
            return_value=httpx.Response(200, json={"data": {"name": "CUST-001"}})
        )
        client = make_client()
        await client.post("/api/resource/Customer", json={"customer_name": "Test"})
        request = mock.calls.last.request
        assert "x-idempotency-key" not in request.headers


# ── Successful response ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_successful_get_returns_data():
    """Successful GET returns the value under the 'data' key."""
    payload = {"name": "ITEM-001", "item_code": "ITEM-001"}
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/resource/Item/ITEM-001").mock(
            return_value=httpx.Response(200, json={"data": payload})
        )
        client = make_client()
        result = await client.get("/api/resource/Item/ITEM-001")
        assert result == payload


# ── Error mapping ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_404_raises_not_found():
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/resource/Customer/NONEXISTENT").mock(
            return_value=erp_error_response(404, "DoesNotExistError", "Customer not found")
        )
        client = make_client()
        with pytest.raises(ERPNextNotFoundError) as exc_info:
            await client.get("/api/resource/Customer/NONEXISTENT")
        assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_401_raises_auth_error():
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/resource/Customer").mock(
            return_value=erp_error_response(401, "AuthenticationError", "Invalid token")
        )
        client = make_client()
        with pytest.raises(ERPNextAuthError) as exc_info:
            await client.get("/api/resource/Customer")
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_417_raises_validation_error():
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/api/resource/Sales Order").mock(
            return_value=erp_error_response(417, "ValidationError", "Missing required field")
        )
        client = make_client()
        with pytest.raises(ERPNextValidationError) as exc_info:
            await client.post("/api/resource/Sales Order", json={})
        assert exc_info.value.status_code == 417


@pytest.mark.asyncio
async def test_422_raises_validation_error():
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/api/resource/Sales Order").mock(
            return_value=erp_error_response(422, "ValidationError", "Invalid data")
        )
        client = make_client()
        with pytest.raises(ERPNextValidationError) as exc_info:
            await client.post("/api/resource/Sales Order", json={})
        assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_duplicate_entry_raises_duplicate_error():
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/api/resource/Customer").mock(
            return_value=erp_error_response(400, "DuplicateEntryError", "Already exists")
        )
        client = make_client()
        with pytest.raises(ERPNextDuplicateError):
            await client.post("/api/resource/Customer", json={"customer_name": "Test"})


# ── Retry behaviour ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retries_on_500_three_times():
    """A 500 response should trigger up to 3 total attempts (2 retries)."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(500, json={"exc_type": "ServerError", "message": "Internal error"})

    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/resource/Item").mock(side_effect=handler)
        client = make_client()
        with pytest.raises(ERPNextError) as exc_info:
            await client.get("/api/resource/Item")
        assert exc_info.value.status_code == 500
        assert call_count == 3


@pytest.mark.asyncio
async def test_no_retry_on_400():
    """A 400/validation error should not be retried — exactly one attempt."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(400, json={"exc_type": "ValidationError", "message": "Bad request"})

    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/api/resource/Sales Order").mock(side_effect=handler)
        client = make_client()
        with pytest.raises(ERPNextValidationError):
            await client.post("/api/resource/Sales Order", json={})
        assert call_count == 1


@pytest.mark.asyncio
async def test_no_retry_on_404():
    """A 404 error should not be retried."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(404, json={"exc_type": "DoesNotExistError", "message": "Not found"})

    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/resource/Customer/NONE").mock(side_effect=handler)
        client = make_client()
        with pytest.raises(ERPNextNotFoundError):
            await client.get("/api/resource/Customer/NONE")
        assert call_count == 1


@pytest.mark.asyncio
async def test_retry_succeeds_on_second_attempt():
    """If the first attempt fails with 500 but the second succeeds, return the result."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(500, json={"exc_type": "ServerError", "message": "Temporary"})
        return httpx.Response(200, json={"data": {"name": "ITEM-001"}})

    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/resource/Item/ITEM-001").mock(side_effect=handler)
        client = make_client()
        result = await client.get("/api/resource/Item/ITEM-001")
        assert result == {"name": "ITEM-001"}
        assert call_count == 2
