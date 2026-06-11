"""
Unit tests for app.mpesa.client.DarajaClient.

All HTTP calls are mocked with respx — no real Daraja credentials needed.
"""
from __future__ import annotations

import base64
from decimal import Decimal

import httpx
import pytest
import respx

from app.mpesa.client import SANDBOX_BASE, DarajaClient, DarajaError

CONSUMER_KEY = "test_ck"
CONSUMER_SECRET = "test_cs"
SHORTCODE = "174379"
PASSKEY = "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919"
CALLBACK_URL = "https://example.com/api/mpesa/callback"

TOKEN = "Bearer_test_token"
CHECKOUT_ID = "ws_CO_123456"
MERCHANT_ID = "MR_789"


def _make_client() -> DarajaClient:
    return DarajaClient(
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        shortcode=SHORTCODE,
        passkey=PASSKEY,
        callback_url=CALLBACK_URL,
        environment="sandbox",
    )


def _oauth_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": TOKEN, "expires_in": "3599"})


def _stk_success() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "MerchantRequestID": MERCHANT_ID,
            "CheckoutRequestID": CHECKOUT_ID,
            "ResponseCode": "0",
            "ResponseDescription": "Success. Request accepted for processing",
            "CustomerMessage": "Success. Request accepted for processing",
        },
    )


# ── OAuth token tests ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_token_calls_oauth_endpoint():
    with respx.mock(base_url=SANDBOX_BASE) as mock:
        mock.get("/oauth/v1/generate").mock(return_value=_oauth_response())
        mock.post("/mpesa/stkpush/v1/processrequest").mock(return_value=_stk_success())

        async with _make_client() as client:
            result = await client.stk_push(
                phone="+254712345678",
                amount=Decimal("100"),
                account_reference="SINV-0001",
            )

        assert result["CheckoutRequestID"] == CHECKOUT_ID
        # OAuth was called exactly once
        assert mock.calls.call_count == 2  # oauth + stk


@pytest.mark.asyncio
async def test_token_is_cached_on_second_call():
    """Two STK pushes in a row should only trigger one OAuth call."""
    with respx.mock(base_url=SANDBOX_BASE) as mock:
        mock.get("/oauth/v1/generate").mock(return_value=_oauth_response())
        mock.post("/mpesa/stkpush/v1/processrequest").mock(return_value=_stk_success())

        async with _make_client() as client:
            await client.stk_push("+254712345678", Decimal("100"), "SINV-0001")
            await client.stk_push("+254712345678", Decimal("200"), "SINV-0002")

        oauth_calls = [c for c in mock.calls if "/oauth/" in str(c.request.url)]
        assert len(oauth_calls) == 1


@pytest.mark.asyncio
async def test_oauth_failure_raises_daraja_error():
    with respx.mock(base_url=SANDBOX_BASE) as mock:
        mock.get("/oauth/v1/generate").mock(
            return_value=httpx.Response(401, text="Unauthorized")
        )

        async with _make_client() as client:
            with pytest.raises(DarajaError, match="OAuth failed"):
                await client.stk_push("+254712345678", Decimal("100"), "SINV-0001")


# ── STK Push tests ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stk_push_happy_path():
    with respx.mock(base_url=SANDBOX_BASE) as mock:
        mock.get("/oauth/v1/generate").mock(return_value=_oauth_response())
        mock.post("/mpesa/stkpush/v1/processrequest").mock(return_value=_stk_success())

        async with _make_client() as client:
            result = await client.stk_push(
                phone="+254712345678",
                amount=Decimal("406"),
                account_reference="SINV-0001",
                transaction_desc="Invoice",
            )

    assert result["CheckoutRequestID"] == CHECKOUT_ID
    assert result["MerchantRequestID"] == MERCHANT_ID
    assert result["ResponseCode"] == "0"


@pytest.mark.asyncio
async def test_stk_push_strips_plus_from_phone():
    captured_body: dict = {}

    def capture(request: httpx.Request) -> httpx.Response:
        import json
        captured_body.update(json.loads(request.content))
        return _stk_success()

    with respx.mock(base_url=SANDBOX_BASE) as mock:
        mock.get("/oauth/v1/generate").mock(return_value=_oauth_response())
        mock.post("/mpesa/stkpush/v1/processrequest").mock(side_effect=capture)

        async with _make_client() as client:
            await client.stk_push("+254712345678", Decimal("100"), "SINV-0001")

    assert captured_body.get("PhoneNumber") == "254712345678"
    assert "+" not in captured_body.get("PhoneNumber", "+")


@pytest.mark.asyncio
async def test_stk_push_per_request_callback_url():
    captured_body: dict = {}

    def capture(request: httpx.Request) -> httpx.Response:
        import json
        captured_body.update(json.loads(request.content))
        return _stk_success()

    custom_url = "https://example.com/api/mpesa/callback/SINV-0001"

    with respx.mock(base_url=SANDBOX_BASE) as mock:
        mock.get("/oauth/v1/generate").mock(return_value=_oauth_response())
        mock.post("/mpesa/stkpush/v1/processrequest").mock(side_effect=capture)

        async with _make_client() as client:
            await client.stk_push(
                "+254712345678", Decimal("100"), "SINV-0001",
                callback_url=custom_url,
            )

    assert captured_body.get("CallBackURL") == custom_url


@pytest.mark.asyncio
async def test_stk_push_daraja_error_response():
    with respx.mock(base_url=SANDBOX_BASE) as mock:
        mock.get("/oauth/v1/generate").mock(return_value=_oauth_response())
        mock.post("/mpesa/stkpush/v1/processrequest").mock(
            return_value=httpx.Response(
                400,
                json={
                    "requestId": "abc",
                    "errorCode": "400.002.02",
                    "errorMessage": "Bad Request - Invalid PhoneNumber",
                },
            )
        )

        async with _make_client() as client:
            with pytest.raises(DarajaError, match="Invalid PhoneNumber"):
                await client.stk_push("+254712345678", Decimal("100"), "SINV-0001")


@pytest.mark.asyncio
async def test_stk_push_response_code_nonzero_raises():
    """ResponseCode != '0' is an application-level error even on HTTP 200."""
    with respx.mock(base_url=SANDBOX_BASE) as mock:
        mock.get("/oauth/v1/generate").mock(return_value=_oauth_response())
        mock.post("/mpesa/stkpush/v1/processrequest").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ResponseCode": "1",
                    "ResponseDescription": "The initiator information is invalid.",
                    "CustomerMessage": "The initiator information is invalid.",
                },
            )
        )

        async with _make_client() as client:
            with pytest.raises(DarajaError):
                await client.stk_push("+254712345678", Decimal("100"), "SINV-0001")


@pytest.mark.asyncio
async def test_stk_push_truncates_account_reference():
    """account_reference is capped at 12 characters by Daraja's limit."""
    captured_body: dict = {}

    def capture(request: httpx.Request) -> httpx.Response:
        import json
        captured_body.update(json.loads(request.content))
        return _stk_success()

    with respx.mock(base_url=SANDBOX_BASE) as mock:
        mock.get("/oauth/v1/generate").mock(return_value=_oauth_response())
        mock.post("/mpesa/stkpush/v1/processrequest").mock(side_effect=capture)

        async with _make_client() as client:
            await client.stk_push(
                "+254712345678", Decimal("100"), "ACC-SINV-2026-000001"
            )

    assert len(captured_body.get("AccountReference", "")) <= 12
