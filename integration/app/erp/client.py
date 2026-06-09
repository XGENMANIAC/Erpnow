"""
ERPNext HTTP client — the ONLY code that talks to ERPNext.

Handles auth, retries with exponential backoff, idempotency headers,
and maps ERPNext error responses to typed Python exceptions.
"""
from __future__ import annotations

import time
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger(__name__)


# ── Typed exceptions ──────────────────────────────────────────────────────────

class ERPNextError(Exception):
    """Base ERPNext error."""
    def __init__(self, message: str, status_code: int | None = None, exc_type: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.exc_type = exc_type


class ERPNextAuthError(ERPNextError):
    """401 — bad or missing API key/secret."""


class ERPNextNotFoundError(ERPNextError):
    """404 — document does not exist."""


class ERPNextValidationError(ERPNextError):
    """400/417 — validation failed (e.g. missing required field)."""


class ERPNextDuplicateError(ERPNextError):
    """409 / DuplicateEntryError — document already exists."""


# ── Retry predicate (only retry on server/rate-limit errors) ─────────────────

def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, ERPNextError):
        return exc.status_code is not None and exc.status_code in (429, 500, 502, 503, 504)
    if isinstance(exc, httpx.TransportError):
        return True
    return False


# ── Client ────────────────────────────────────────────────────────────────────

class ERPNextClient:
    """Async HTTP client for the ERPNext REST API."""

    def __init__(self, base_url: str, api_key: str, api_secret: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth_header = f"token {api_key}:{api_secret}"
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": self._auth_header,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=30.0,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> ERPNextClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    # ── Low-level request ─────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        headers: dict[str, str] = {}
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key

        t0 = time.monotonic()
        try:
            response = await self._http.request(
                method,
                path,
                params=params,
                json=json,
                headers=headers,
            )
        except httpx.TransportError as exc:
            logger.warning("erp_transport_error", path=path, error=str(exc))
            raise

        duration_ms = round((time.monotonic() - t0) * 1000)
        logger.debug(
            "erp_request",
            method=method,
            path=path,
            status=response.status_code,
            duration_ms=duration_ms,
        )

        return self._parse_response(response)

    def _parse_response(self, response: httpx.Response) -> Any:
        status = response.status_code

        if status == 200 or status == 201:
            body = response.json()
            # ERPNext wraps results under "data" key
            return body.get("data", body)

        # Parse error body
        exc_type: str | None = None
        message: str = response.text
        try:
            body = response.json()
            exc_type = body.get("exc_type") or body.get("exception", "")
            message = body.get("message") or body.get("_error_message") or message
            # ERPNext sometimes nests errors
            if not message and "exc" in body:
                message = body["exc"]
        except Exception:
            pass

        # Duplicate check applies regardless of HTTP status code
        if exc_type and "Duplicate" in exc_type:
            raise ERPNextDuplicateError(message, status_code=status, exc_type=exc_type)

        match status:
            case 401:
                raise ERPNextAuthError(message, status_code=status, exc_type=exc_type)
            case 404:
                raise ERPNextNotFoundError(message, status_code=status, exc_type=exc_type)
            case 400 | 417 | 422:
                raise ERPNextValidationError(message, status_code=status, exc_type=exc_type)
            case 409:
                raise ERPNextDuplicateError(message, status_code=status, exc_type=exc_type)
            case _:
                raise ERPNextError(message, status_code=status, exc_type=exc_type)

    # ── Retry-wrapped public methods ──────────────────────────────────────────

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def post(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        return await self._request("POST", path, json=json, idempotency_key=idempotency_key)

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def put(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        return await self._request("PUT", path, json=json, idempotency_key=idempotency_key)
