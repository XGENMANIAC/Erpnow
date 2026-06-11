"""
Safaricom Daraja M-Pesa API client.

Handles:
  - OAuth2 client-credentials token acquisition with in-process caching.
  - STK Push (Lipa Na M-Pesa Online Express).
"""
from __future__ import annotations

import base64
import time
from datetime import datetime
from decimal import Decimal

import httpx
import structlog

logger = structlog.get_logger(__name__)

SANDBOX_BASE = "https://sandbox.safaricom.co.ke"
PRODUCTION_BASE = "https://api.safaricom.co.ke"

_OAUTH_PATH = "/oauth/v1/generate"
_STK_PUSH_PATH = "/mpesa/stkpush/v1/processrequest"


class DarajaError(Exception):
    """Raised when Daraja returns an error or unexpected response."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class DarajaClient:
    """
    Async Daraja API client.

    Token caching: one token is held in memory and refreshed when it
    expires.  The token is NOT shared across instances (use a singleton
    or dependency-inject to share state).
    """

    def __init__(
        self,
        consumer_key: str,
        consumer_secret: str,
        shortcode: str,
        passkey: str,
        callback_url: str,
        environment: str = "sandbox",
    ) -> None:
        self._consumer_key = consumer_key
        self._consumer_secret = consumer_secret
        self._shortcode = shortcode
        self._passkey = passkey
        self._callback_url = callback_url
        base_url = SANDBOX_BASE if environment != "production" else PRODUCTION_BASE
        self._http = httpx.AsyncClient(base_url=base_url, timeout=30.0)
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> DarajaClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    # ── OAuth ─────────────────────────────────────────────────────────────────

    async def _get_token(self) -> str:
        """Return a valid bearer token, refreshing if within 60 s of expiry."""
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token

        creds = base64.b64encode(
            f"{self._consumer_key}:{self._consumer_secret}".encode()
        ).decode()

        response = await self._http.get(
            _OAUTH_PATH,
            params={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {creds}"},
        )
        if response.status_code != 200:
            raise DarajaError(f"OAuth failed ({response.status_code}): {response.text}")

        body = response.json()
        if "access_token" not in body:
            raise DarajaError(f"OAuth response missing access_token: {body}")

        self._token = body["access_token"]
        # Expire 60 s early to avoid using a token right at the edge
        ttl = int(body.get("expires_in", 3599))
        self._token_expires_at = time.monotonic() + ttl - 60
        logger.debug("daraja_token_refreshed", ttl=ttl)
        return self._token

    # ── STK Push ──────────────────────────────────────────────────────────────

    def _make_password(self, timestamp: str) -> str:
        """Lipa Na M-Pesa Online password: base64(shortcode + passkey + timestamp)."""
        raw = f"{self._shortcode}{self._passkey}{timestamp}"
        return base64.b64encode(raw.encode()).decode()

    async def stk_push(
        self,
        phone: str,
        amount: Decimal,
        account_reference: str,
        transaction_desc: str = "Payment",
        callback_url: str | None = None,
    ) -> dict:
        """
        Initiate an STK push.

        phone: E.164 (+254XXXXXXXXX) or 254XXXXXXXXX — leading + is stripped.
        Returns Daraja's raw response dict on success; raises DarajaError on failure.
        """
        token = await self._get_token()
        phone_clean = phone.lstrip("+")
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        password = self._make_password(timestamp)

        payload = {
            "BusinessShortCode": self._shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": int(amount),
            "PartyA": phone_clean,
            "PartyB": self._shortcode,
            "PhoneNumber": phone_clean,
            "CallBackURL": callback_url or self._callback_url,
            "AccountReference": account_reference[:12],
            "TransactionDesc": transaction_desc[:13],
        }

        response = await self._http.post(
            _STK_PUSH_PATH,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        body = response.json()

        if response.status_code != 200 or body.get("ResponseCode") != "0":
            error_msg = (
                body.get("errorMessage")
                or body.get("CustomerMessage")
                or f"STK push failed (HTTP {response.status_code})"
            )
            raise DarajaError(error_msg, code=body.get("errorCode") or body.get("ResponseCode"))

        logger.info(
            "stk_push_initiated",
            checkout_request_id=body.get("CheckoutRequestID"),
            phone=phone,
            amount=str(amount),
        )
        return body
