"""
Channel gateway stub.

Phase 2 will implement:
  - Inbound webhook handlers for WhatsApp, SMS, and web chat.
  - Outbound message dispatch with per-channel adapters.
  - Session/conversation state management.
"""
from __future__ import annotations


class ChannelGateway:
    """Stub — will be implemented in Phase 2."""

    async def dispatch(self, channel: str, recipient: str, message: str) -> None:
        raise NotImplementedError("Channel gateway coming in Phase 2")

    async def handle_inbound(self, channel: str, payload: dict) -> None:
        raise NotImplementedError("Channel gateway coming in Phase 2")
