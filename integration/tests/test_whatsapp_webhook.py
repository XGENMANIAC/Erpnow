"""
Tests for the WhatsApp webhook FastAPI endpoints.

GET  /api/whatsapp/webhook  — verification handshake
POST /api/whatsapp/webhook  — inbound messages

ChannelGateway.handle_inbound is patched so no DB is needed here.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.db.session import get_session
from app.main import app


@pytest.fixture(autouse=True)
def override_db_session():
    """Override the get_session dependency so tests don't need a real DB."""
    async def _fake_session():
        yield MagicMock()

    app.dependency_overrides[get_session] = _fake_session
    yield
    app.dependency_overrides.pop(get_session, None)

VERIFY_TOKEN = "dewmix_verify_token_test"
WEBHOOK_SECRET = "dewmix_webhook_secret_test"
WAID = "254712345678"


def _sign_body(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _text_body(msg_id: str = "wamid.TEST001", text: str = "Nataka mabati") -> bytes:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WBA_ID",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"phone_number_id": "PH_ID"},
                    "contacts": [{"profile": {"name": "Bob"}, "wa_id": WAID}],
                    "messages": [{
                        "from": WAID,
                        "id": msg_id,
                        "timestamp": "1699999999",
                        "type": "text",
                        "text": {"body": text},
                    }],
                },
            }],
        }],
    }
    return json.dumps(payload).encode()


def _mock_gateway(processed_count: int = 1):
    """Patch ChannelGateway.handle_inbound to return dummy processed list."""
    result = [{"message_id": f"wamid.{i}"} for i in range(processed_count)]
    return patch(
        "app.api.whatsapp_webhook.ChannelGateway.handle_inbound",
        new=AsyncMock(return_value=result),
    )


def _mock_settings_tokens(verify_token: str = VERIFY_TOKEN, webhook_secret: str = WEBHOOK_SECRET):
    """Override the relevant settings fields."""
    from unittest.mock import patch as _patch
    return _patch.multiple(
        "app.api.whatsapp_webhook.settings",
        whatsapp_verify_token=verify_token,
        whatsapp_webhook_secret=webhook_secret,
    )


# ── GET /api/whatsapp/webhook — verification ──────────────────────────────────

def test_verify_webhook_valid_token():
    with _mock_settings_tokens(), TestClient(app) as client:
        resp = client.get(
            "/api/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "challenge_xyz",
                "hub.verify_token": VERIFY_TOKEN,
            },
        )
    assert resp.status_code == 200
    assert resp.text == "challenge_xyz"


def test_verify_webhook_wrong_token():
    with _mock_settings_tokens(), TestClient(app) as client:
        resp = client.get(
            "/api/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "challenge_xyz",
                "hub.verify_token": "wrong_token",
            },
        )
    assert resp.status_code == 403


def test_verify_webhook_wrong_mode():
    with _mock_settings_tokens(), TestClient(app) as client:
        resp = client.get(
            "/api/whatsapp/webhook",
            params={
                "hub.mode": "unsubscribe",
                "hub.challenge": "challenge_xyz",
                "hub.verify_token": VERIFY_TOKEN,
            },
        )
    assert resp.status_code == 403


def test_verify_webhook_missing_params():
    with TestClient(app) as client:
        resp = client.get("/api/whatsapp/webhook")
    assert resp.status_code == 403


# ── POST /api/whatsapp/webhook — inbound messages ─────────────────────────────

def test_post_valid_message_with_correct_signature():
    body = _text_body()
    sig = _sign_body(body)

    with _mock_settings_tokens(), _mock_gateway(1) as gw_mock, TestClient(app) as client:
        resp = client.post(
            "/api/whatsapp/webhook",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["processed"] == 1
    gw_mock.assert_awaited_once()


def test_post_invalid_signature_returns_403():
    body = _text_body()
    bad_sig = "sha256=deadbeef0000"

    with _mock_settings_tokens(), TestClient(app) as client:
        resp = client.post(
            "/api/whatsapp/webhook",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": bad_sig},
        )

    assert resp.status_code == 403


def test_post_missing_signature_when_secret_set_returns_403():
    body = _text_body()

    with _mock_settings_tokens(), TestClient(app) as client:
        resp = client.post(
            "/api/whatsapp/webhook",
            content=body,
            headers={"Content-Type": "application/json"},
            # No X-Hub-Signature-256 header
        )

    assert resp.status_code == 403


def test_post_no_secret_configured_skips_hmac():
    """When WHATSAPP_WEBHOOK_SECRET is empty, HMAC check is skipped."""
    body = _text_body()

    with _mock_settings_tokens(webhook_secret=""), _mock_gateway(1) as gw_mock, TestClient(app) as client:
        resp = client.post(
            "/api/whatsapp/webhook",
            content=body,
            headers={"Content-Type": "application/json"},
        )

    assert resp.status_code == 200
    gw_mock.assert_awaited_once()


def test_post_invalid_json_returns_200():
    """Malformed JSON should not crash; always return 200."""
    with _mock_settings_tokens(webhook_secret=""), TestClient(app) as client:
        resp = client.post(
            "/api/whatsapp/webhook",
            content=b"not json at all",
            headers={"Content-Type": "application/json"},
        )

    assert resp.status_code == 200


def test_post_gateway_error_still_returns_200():
    """Gateway exceptions must not propagate as 5xx — Meta would retry."""
    body = _text_body()

    failing_gateway = AsyncMock(side_effect=RuntimeError("DB exploded"))
    with (
        _mock_settings_tokens(webhook_secret=""),
        patch("app.api.whatsapp_webhook.ChannelGateway.handle_inbound", failing_gateway),
        TestClient(app) as client,
    ):
        resp = client.post(
            "/api/whatsapp/webhook",
            content=body,
            headers={"Content-Type": "application/json"},
        )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_post_correct_signature_constructed_from_body():
    """Signature built from a different body should be rejected."""
    real_body = _text_body(text="real")
    tampered_body = _text_body(text="tampered")
    sig_for_real = _sign_body(real_body)

    with _mock_settings_tokens(), TestClient(app) as client:
        resp = client.post(
            "/api/whatsapp/webhook",
            content=tampered_body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig_for_real},
        )

    assert resp.status_code == 403


def test_post_status_update_returns_200_with_zero_processed():
    """Status updates produce no messages; endpoint still returns 200."""
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WBA_ID",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"phone_number_id": "PH_ID"},
                    "statuses": [{"id": "wamid.X", "status": "read"}],
                },
            }],
        }],
    }
    body = json.dumps(payload).encode()

    with _mock_settings_tokens(webhook_secret=""), _mock_gateway(0) as gw_mock, TestClient(app) as client:
        resp = client.post(
            "/api/whatsapp/webhook",
            content=body,
            headers={"Content-Type": "application/json"},
        )

    assert resp.status_code == 200
    assert resp.json()["processed"] == 0
