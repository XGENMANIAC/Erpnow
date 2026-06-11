"""
WhatsApp Cloud API client, payload parser, and HMAC verification.

Handles:
  - Inbound webhook parsing (text, image, audio, video, document, interactive)
  - HMAC-SHA256 signature verification for inbound webhooks
  - Outbound text and template message sending via the Graph API
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import structlog

logger = structlog.get_logger(__name__)

GRAPH_BASE = "https://graph.facebook.com/v19.0"

# Message types where Meta sends media content
_MEDIA_TYPES = frozenset({"image", "audio", "video", "document", "sticker", "voice"})


class WhatsAppError(Exception):
    """Raised when the Graph API returns an error."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


# ── Inbound message model ─────────────────────────────────────────────────────

@dataclass
class InboundMessage:
    """Normalised representation of a single inbound WhatsApp message."""

    message_id: str
    waid: str              # sender's WhatsApp ID (E.164 without +, e.g. "254712345678")
    display_name: str
    message_type: str      # text|image|audio|video|document|interactive|button|...
    text: str | None       # body text (or caption for media, or button title)
    media_id: str | None   # WhatsApp media ID for downloadable content
    timestamp: datetime    # UTC
    phone_number_id: str   # our receiving phone number ID


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_inbound_webhook(payload: dict) -> list[InboundMessage]:
    """
    Flatten a WhatsApp Cloud API webhook payload into a list of InboundMessage.

    Handles the nested entry → changes → value → messages structure.
    Status updates (no messages field) are silently skipped.
    """
    result: list[InboundMessage] = []

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change.get("value", {})
            if "messages" not in value:
                continue

            phone_number_id = value.get("metadata", {}).get("phone_number_id", "")

            # Build waid → display_name map from contacts
            contacts_map: dict[str, str] = {
                c["wa_id"]: c.get("profile", {}).get("name", "")
                for c in value.get("contacts", [])
            }

            for msg in value["messages"]:
                waid = msg.get("from", "")
                msg_type = msg.get("type", "text")
                text: str | None = None
                media_id: str | None = None

                if msg_type == "text":
                    text = msg.get("text", {}).get("body")
                elif msg_type in _MEDIA_TYPES:
                    media_block = msg.get(msg_type, {})
                    media_id = media_block.get("id")
                    text = media_block.get("caption")
                elif msg_type == "interactive":
                    itype = msg.get("interactive", {}).get("type", "")
                    if itype == "button_reply":
                        text = msg["interactive"]["button_reply"]["title"]
                    elif itype == "list_reply":
                        text = msg["interactive"]["list_reply"]["title"]
                elif msg_type == "button":
                    text = msg.get("button", {}).get("text")
                elif msg_type == "location":
                    loc = msg.get("location", {})
                    text = f"{loc.get('latitude')},{loc.get('longitude')}"
                # else: reaction, order, contacts — text stays None

                result.append(
                    InboundMessage(
                        message_id=msg["id"],
                        waid=waid,
                        display_name=contacts_map.get(waid, waid),
                        message_type=msg_type,
                        text=text,
                        media_id=media_id,
                        timestamp=datetime.fromtimestamp(
                            int(msg.get("timestamp", 0)), tz=timezone.utc
                        ),
                        phone_number_id=phone_number_id,
                    )
                )

    return result


# ── HMAC signature verification ───────────────────────────────────────────────

def verify_signature(payload_bytes: bytes, signature_header: str, secret: str) -> bool:
    """
    Verify the X-Hub-Signature-256 header from Meta.

    Returns True when the header is present, correctly formed, and the HMAC
    matches.  Returns False on any mismatch or malformed header.
    """
    if not signature_header.startswith("sha256="):
        return False
    received_hex = signature_header[len("sha256="):]
    expected_hex = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_hex, received_hex)


# ── WhatsApp Cloud API client ─────────────────────────────────────────────────

class WhatsAppClient:
    """
    Async client for the WhatsApp Cloud API (Meta Graph v19).

    Sends text and template messages.  All operations are async and safe
    to use as an async context manager.
    """

    def __init__(self, access_token: str, phone_number_id: str) -> None:
        self._phone_number_id = phone_number_id
        self._http = httpx.AsyncClient(
            base_url=GRAPH_BASE,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> WhatsAppClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    def _clean_phone(self, phone: str) -> str:
        """Strip leading + and spaces: '+254 712...' → '254712...'"""
        return phone.lstrip("+").replace(" ", "")

    async def send_text(self, to: str, text: str) -> dict:
        """
        Send a plain text message.

        Returns the Graph API response dict (contains the messages[0].id).
        Raises WhatsAppError on non-200 response.
        """
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self._clean_phone(to),
            "type": "text",
            "text": {"body": text, "preview_url": False},
        }
        resp = await self._http.post(
            f"/{self._phone_number_id}/messages",
            json=payload,
        )
        if resp.status_code not in (200, 201):
            error_data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            raise WhatsAppError(
                error_data.get("error", {}).get("message", resp.text),
                code=resp.status_code,
            )
        body = resp.json()
        msg_id = (body.get("messages") or [{}])[0].get("id", "")
        logger.info("whatsapp_sent", to=to, message_id=msg_id)
        return body

    async def send_template(
        self,
        to: str,
        template_name: str,
        language: str = "en",
        components: list[dict] | None = None,
    ) -> dict:
        """
        Send a pre-approved template message.

        `components` is the list of header/body/button parameter objects
        as defined by the Meta API.
        """
        template: dict = {"name": template_name, "language": {"code": language}}
        if components:
            template["components"] = components

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self._clean_phone(to),
            "type": "template",
            "template": template,
        }
        resp = await self._http.post(
            f"/{self._phone_number_id}/messages",
            json=payload,
        )
        if resp.status_code not in (200, 201):
            error_data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            raise WhatsAppError(
                error_data.get("error", {}).get("message", resp.text),
                code=resp.status_code,
            )
        logger.info("whatsapp_template_sent", to=to, template=template_name)
        return resp.json()
