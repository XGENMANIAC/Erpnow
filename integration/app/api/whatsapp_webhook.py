"""
WhatsApp Cloud API webhook endpoints.

GET  /api/whatsapp/webhook  — Meta verification handshake
POST /api/whatsapp/webhook  — Inbound messages and status updates

Security:
  - Verification token checked on GET.
  - HMAC-SHA256 (X-Hub-Signature-256) checked on POST when
    WHATSAPP_WEBHOOK_SECRET is set; requests without/wrong signature
    receive 403.
"""
from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.gateway import ChannelGateway
from app.channels.whatsapp import verify_signature
from app.config import settings
from app.db.session import get_session, get_session_factory

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])


# ── Verification handshake ────────────────────────────────────────────────────

@router.get("/webhook")
async def verify_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
) -> Response:
    """
    Respond to Meta's webhook verification challenge.

    Meta sends GET with hub.mode=subscribe and the token we configured.
    We echo back hub.challenge as plain text to complete verification.
    """
    if (
        hub_mode == "subscribe"
        and hub_verify_token
        and hub_verify_token == settings.whatsapp_verify_token
        and hub_challenge
    ):
        return Response(content=hub_challenge, media_type="text/plain")

    logger.warning(
        "whatsapp_verify_failed",
        hub_mode=hub_mode,
        token_match=hub_verify_token == settings.whatsapp_verify_token,
    )
    return Response(status_code=403)


# ── Inbound webhook ───────────────────────────────────────────────────────────

async def _run_agent_background(
    conversation_id: int,
    waid: str,
    message_text: str,
) -> None:
    """
    Background task: run the agent in its own DB session.

    The request-scoped session has already been committed and closed by the
    time this runs, so we open a fresh one from the factory.
    """
    from app.agents.router import AgentRouter

    factory = get_session_factory()
    async with factory() as session:
        try:
            router_instance = AgentRouter()
            await router_instance.handle(
                conversation_id=conversation_id,
                waid=waid,
                user_message=message_text,
                session=session,
            )
            await session.commit()
        except Exception as exc:
            logger.error(
                "agent_background_error",
                conversation_id=conversation_id,
                error=str(exc),
            )


@router.post("/webhook")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """
    Receive inbound WhatsApp messages and status updates.

    Always returns HTTP 200 after signature verification — Meta retries
    non-200 responses, which would cause duplicate processing.
    """
    body_bytes = await request.body()

    # ── HMAC verification ──────────────────────────────────────────────────────
    if settings.whatsapp_webhook_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not verify_signature(body_bytes, signature, settings.whatsapp_webhook_secret):
            logger.warning("whatsapp_invalid_signature", remote=request.client)
            return JSONResponse({"error": "Invalid signature"}, status_code=403)

    # ── Parse JSON ─────────────────────────────────────────────────────────────
    try:
        payload = json.loads(body_bytes)
    except json.JSONDecodeError as exc:
        logger.warning("whatsapp_invalid_json", error=str(exc))
        return JSONResponse({"ok": True})

    # ── Process messages ───────────────────────────────────────────────────────
    gateway = ChannelGateway()
    try:
        processed = await gateway.handle_inbound("whatsapp", payload, session)
        logger.info("whatsapp_webhook_ok", processed=len(processed))

        # Trigger the agent for each stored inbound text message
        for msg_summary in processed:
            if msg_summary.get("text"):
                background_tasks.add_task(
                    _run_agent_background,
                    conversation_id=msg_summary["conversation_id"],
                    waid=msg_summary["waid"],
                    message_text=msg_summary["text"],
                )

        return JSONResponse({"ok": True, "processed": len(processed)})
    except Exception as exc:
        logger.error("whatsapp_gateway_error", error=str(exc))
        # Still return 200 — Meta would retry on non-200
        return JSONResponse({"ok": True, "processed": 0})
