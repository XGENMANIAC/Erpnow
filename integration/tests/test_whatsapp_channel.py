"""
Unit tests for the WhatsApp channel module.

Covers:
  - parse_inbound_webhook: text, image, interactive, status-only, multi-entry
  - verify_signature: valid and invalid HMAC
  - WhatsAppClient.send_text: happy path, error response
  - WhatsAppClient.send_template: happy path
  - ChannelGateway.handle_inbound: conversation upsert, message storage, dedup
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
import respx

from app.channels.gateway import ChannelGateway
from app.channels.whatsapp import (
    InboundMessage,
    WhatsAppClient,
    WhatsAppError,
    GRAPH_BASE,
    parse_inbound_webhook,
    verify_signature,
)
from app.db.models import Conversation, Message

PHONE_ID = "1234567890"
ACCESS_TOKEN = "test_token"
WAID = "254712345678"
DISPLAY_NAME = "Alice Wanjiku"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _text_payload(
    waid: str = WAID,
    name: str = DISPLAY_NAME,
    body: str = "Hello DEWMIX",
    msg_id: str = "wamid.TEXT001",
    timestamp: str = "1699999999",
) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WBA_ID",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "254787151516", "phone_number_id": PHONE_ID},
                    "contacts": [{"profile": {"name": name}, "wa_id": waid}],
                    "messages": [{
                        "from": waid,
                        "id": msg_id,
                        "timestamp": timestamp,
                        "type": "text",
                        "text": {"body": body},
                    }],
                },
            }],
        }],
    }


def _image_payload(waid: str = WAID, msg_id: str = "wamid.IMG001") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WBA_ID",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"phone_number_id": PHONE_ID},
                    "contacts": [{"profile": {"name": DISPLAY_NAME}, "wa_id": waid}],
                    "messages": [{
                        "from": waid,
                        "id": msg_id,
                        "timestamp": "1699999999",
                        "type": "image",
                        "image": {"id": "IMG_MEDIA_ID", "mime_type": "image/jpeg", "caption": "My roof"},
                    }],
                },
            }],
        }],
    }


def _button_reply_payload(waid: str = WAID) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WBA_ID",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"phone_number_id": PHONE_ID},
                    "contacts": [{"profile": {"name": DISPLAY_NAME}, "wa_id": waid}],
                    "messages": [{
                        "from": waid,
                        "id": "wamid.BTN001",
                        "timestamp": "1699999999",
                        "type": "interactive",
                        "interactive": {
                            "type": "button_reply",
                            "button_reply": {"id": "btn_1", "title": "Yes, confirm order"},
                        },
                    }],
                },
            }],
        }],
    }


def _status_payload() -> dict:
    """Webhook with only a status update (no messages)."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WBA_ID",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"phone_number_id": PHONE_ID},
                    "statuses": [{
                        "id": "wamid.TEXT001",
                        "status": "delivered",
                        "timestamp": "1699999999",
                        "recipient_id": WAID,
                    }],
                },
            }],
        }],
    }


def _sign(payload_bytes: bytes, secret: str = "testsecret") -> str:
    digest = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _wa_client() -> WhatsAppClient:
    return WhatsAppClient(access_token=ACCESS_TOKEN, phone_number_id=PHONE_ID)


def _send_success(msg_id: str = "wamid.OUT001") -> httpx.Response:
    return httpx.Response(200, json={
        "messaging_product": "whatsapp",
        "contacts": [{"input": WAID, "wa_id": WAID}],
        "messages": [{"id": msg_id}],
    })


# ── parse_inbound_webhook ─────────────────────────────────────────────────────

def test_parse_text_message():
    msgs = parse_inbound_webhook(_text_payload())
    assert len(msgs) == 1
    m = msgs[0]
    assert m.message_id == "wamid.TEXT001"
    assert m.waid == WAID
    assert m.display_name == DISPLAY_NAME
    assert m.message_type == "text"
    assert m.text == "Hello DEWMIX"
    assert m.media_id is None
    assert m.phone_number_id == PHONE_ID
    assert isinstance(m.timestamp, datetime)
    assert m.timestamp.tzinfo == timezone.utc


def test_parse_image_message():
    msgs = parse_inbound_webhook(_image_payload())
    assert len(msgs) == 1
    m = msgs[0]
    assert m.message_type == "image"
    assert m.media_id == "IMG_MEDIA_ID"
    assert m.text == "My roof"


def test_parse_interactive_button_reply():
    msgs = parse_inbound_webhook(_button_reply_payload())
    assert len(msgs) == 1
    m = msgs[0]
    assert m.message_type == "interactive"
    assert m.text == "Yes, confirm order"
    assert m.media_id is None


def test_parse_status_update_skipped():
    """Status-only webhooks produce no InboundMessages."""
    msgs = parse_inbound_webhook(_status_payload())
    assert msgs == []


def test_parse_empty_payload():
    msgs = parse_inbound_webhook({})
    assert msgs == []


def test_parse_multiple_messages_in_one_change():
    payload = _text_payload(msg_id="wamid.A")
    payload["entry"][0]["changes"][0]["value"]["messages"].append({
        "from": WAID,
        "id": "wamid.B",
        "timestamp": "1700000000",
        "type": "text",
        "text": {"body": "Second message"},
    })
    msgs = parse_inbound_webhook(payload)
    assert len(msgs) == 2
    assert {m.message_id for m in msgs} == {"wamid.A", "wamid.B"}


def test_parse_unknown_contact_falls_back_to_waid():
    """If contacts block is absent, display_name should be the waid."""
    payload = _text_payload()
    del payload["entry"][0]["changes"][0]["value"]["contacts"]
    msgs = parse_inbound_webhook(payload)
    assert msgs[0].display_name == WAID


# ── verify_signature ──────────────────────────────────────────────────────────

def test_verify_signature_valid():
    data = b'{"test": "payload"}'
    sig = _sign(data, "secret123")
    assert verify_signature(data, sig, "secret123") is True


def test_verify_signature_wrong_secret():
    data = b'{"test": "payload"}'
    sig = _sign(data, "correct_secret")
    assert verify_signature(data, sig, "wrong_secret") is False


def test_verify_signature_tampered_body():
    data = b'{"test": "payload"}'
    sig = _sign(data, "secret")
    tampered = b'{"test": "different"}'
    assert verify_signature(tampered, sig, "secret") is False


def test_verify_signature_missing_sha256_prefix():
    assert verify_signature(b"data", "noshaprefix=abc", "secret") is False


def test_verify_signature_empty_header():
    assert verify_signature(b"data", "", "secret") is False


# ── WhatsAppClient ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_text_happy_path():
    with respx.mock(base_url=GRAPH_BASE) as mock:
        mock.post(f"/{PHONE_ID}/messages").mock(return_value=_send_success())

        async with _wa_client() as wa:
            result = await wa.send_text(to=f"+{WAID}", text="Habari! Order yako iko tayari.")

    assert result["messages"][0]["id"] == "wamid.OUT001"


@pytest.mark.asyncio
async def test_send_text_strips_plus_from_to():
    captured_body: dict = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return _send_success()

    with respx.mock(base_url=GRAPH_BASE) as mock:
        mock.post(f"/{PHONE_ID}/messages").mock(side_effect=capture)

        async with _wa_client() as wa:
            await wa.send_text(to="+254712345678", text="Hello")

    assert captured_body.get("to") == "254712345678"


@pytest.mark.asyncio
async def test_send_text_authorization_header():
    captured_headers: dict = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(request.headers))
        return _send_success()

    with respx.mock(base_url=GRAPH_BASE) as mock:
        mock.post(f"/{PHONE_ID}/messages").mock(side_effect=capture)

        async with _wa_client() as wa:
            await wa.send_text(to=WAID, text="Hello")

    assert captured_headers.get("authorization") == f"Bearer {ACCESS_TOKEN}"


@pytest.mark.asyncio
async def test_send_text_error_raises_whatsapp_error():
    with respx.mock(base_url=GRAPH_BASE) as mock:
        mock.post(f"/{PHONE_ID}/messages").mock(
            return_value=httpx.Response(
                400,
                json={"error": {"message": "Invalid phone number", "code": 131026}},
            )
        )

        async with _wa_client() as wa:
            with pytest.raises(WhatsAppError, match="Invalid phone number"):
                await wa.send_text(to=WAID, text="Hello")


@pytest.mark.asyncio
async def test_send_template_happy_path():
    with respx.mock(base_url=GRAPH_BASE) as mock:
        mock.post(f"/{PHONE_ID}/messages").mock(return_value=_send_success("wamid.TPL001"))

        async with _wa_client() as wa:
            result = await wa.send_template(
                to=WAID,
                template_name="hello_world",
                language="en_US",
            )

    assert result["messages"][0]["id"] == "wamid.TPL001"


# ── ChannelGateway.handle_inbound ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gateway_stores_message(test_db_session):
    gateway = ChannelGateway()
    processed = await gateway.handle_inbound("whatsapp", _text_payload(), test_db_session)

    assert len(processed) == 1
    assert processed[0]["message_id"] == "wamid.TEXT001"
    assert processed[0]["waid"] == WAID


@pytest.mark.asyncio
async def test_gateway_creates_conversation(test_db_session):
    from sqlalchemy import select
    gateway = ChannelGateway()
    await gateway.handle_inbound("whatsapp", _text_payload(), test_db_session)

    result = await test_db_session.execute(select(Conversation))
    convs = result.scalars().all()
    assert len(convs) == 1
    assert convs[0].waid == WAID
    assert convs[0].channel == "whatsapp"
    assert convs[0].display_name == DISPLAY_NAME
    assert convs[0].phone == f"+{WAID}"


@pytest.mark.asyncio
async def test_gateway_deduplicates_same_message_id(test_db_session):
    """Delivering the same message_id twice should process it exactly once."""
    gateway = ChannelGateway()
    payload = _text_payload(msg_id="wamid.DEDUP")

    first = await gateway.handle_inbound("whatsapp", payload, test_db_session)
    second = await gateway.handle_inbound("whatsapp", payload, test_db_session)

    assert len(first) == 1
    assert len(second) == 0

    from sqlalchemy import select
    result = await test_db_session.execute(
        select(Message).where(Message.message_id == "wamid.DEDUP")
    )
    assert len(result.scalars().all()) == 1


@pytest.mark.asyncio
async def test_gateway_reuses_existing_conversation(test_db_session):
    """Two messages from the same waid use the same Conversation row."""
    from sqlalchemy import select, func

    gateway = ChannelGateway()
    await gateway.handle_inbound("whatsapp", _text_payload(msg_id="wamid.M1"), test_db_session)
    await gateway.handle_inbound("whatsapp", _text_payload(msg_id="wamid.M2"), test_db_session)

    result = await test_db_session.execute(select(func.count()).select_from(Conversation))
    assert result.scalar() == 1

    result2 = await test_db_session.execute(select(func.count()).select_from(Message))
    assert result2.scalar() == 2


@pytest.mark.asyncio
async def test_gateway_status_update_no_messages_stored(test_db_session):
    gateway = ChannelGateway()
    processed = await gateway.handle_inbound("whatsapp", _status_payload(), test_db_session)
    assert processed == []

    from sqlalchemy import select, func
    result = await test_db_session.execute(select(func.count()).select_from(Message))
    assert result.scalar() == 0


@pytest.mark.asyncio
async def test_gateway_unsupported_channel_raises():
    gateway = ChannelGateway()
    with pytest.raises(ValueError, match="Unsupported channel"):
        await gateway.handle_inbound("telegram", {}, None)
