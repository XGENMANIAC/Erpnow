"""
ConversationAgent — drives the CRM chat loop for a single conversation turn.

Uses NimClient for the LLM and ERP tools for real-time data lookup.
Returns an AgentResponse indicating what action to take next.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.memory import load_history, save_assistant_reply
from app.agents.nim_client import NimClient
from app.tools.registry import TERMINAL_TOOL_NAMES, TERMINAL_TOOLS, build_erp_tools

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = """\
You are Dewmix, a helpful sales assistant for DEWMIX Hardware — a Kenyan \
hardware shop on Nyeri Highway.

You help customers order: roofing sheets (mabati), nails, cement, timber, \
wire mesh, PVC pipes, paint, and other hardware products.

Rules you MUST follow:
1. Always look up prices using get_price — never quote from memory.
2. Always check stock using check_stock before confirming availability.
3. Use search_catalog when the customer names a product but you don't have \
   the item code.
4. When the customer clearly confirms their order (says yes / ndiyo / sawa \
   / confirm / ndio), call confirm_order with the agreed items and quantities.
5. When the customer asks for a price quote but is NOT ready to order, call \
   request_quote.
6. Call escalate_to_human when: the customer is frustrated, the order total \
   is likely over KES 50,000, you are unsure about specs, or the customer \
   asks about credit terms, bulk discounts, or delivery outside Nairobi/Nyeri.
7. Keep replies brief and in the customer's language (Swahili or English).
8. Never compute or guess prices — only quote what get_price returns.
9. Never confirm stock unless check_stock shows actual_qty > 0.
"""


@dataclass
class AgentResponse:
    action: str  # "reply" | "order_confirmed" | "quote_requested" | "escalate" | "error"
    text: str
    data: dict = field(default_factory=dict)
    total_tokens: int = 0
    tool_calls_made: int = 0


class ConversationAgent:
    """
    Runs one conversation turn: loads history, calls NIM, handles tool loop,
    returns AgentResponse.
    """

    def __init__(
        self,
        nim_client: NimClient,
        erp_client: Any,
        settings: Any,
    ) -> None:
        self._nim = nim_client
        self._erp = erp_client
        self._settings = settings

    async def run(
        self,
        conversation_id: int,
        user_message: str,
        session: AsyncSession,
        history_limit: int = 20,
    ) -> AgentResponse:
        """
        Process one inbound message and return an AgentResponse.

        Loads conversation history, appends the new user message, runs the
        NIM tool loop, and persists the assistant reply on a text response.
        """
        try:
            history = await load_history(conversation_id, session, limit=history_limit)
        except Exception as exc:
            logger.warning("load_history_failed", conversation_id=conversation_id, error=str(exc))
            history = []

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        erp_registry = build_erp_tools(self._erp, self._settings)

        async def _tool_executor(tc):
            return await erp_registry.execute(tc.name, tc.arguments)

        all_tools = erp_registry.to_openai_format() + TERMINAL_TOOLS

        try:
            turn = await self._nim.run_turn(
                messages=messages,
                tools=all_tools,
                tool_executor=_tool_executor,
                terminal_tool_names=TERMINAL_TOOL_NAMES,
            )
        except Exception as exc:
            logger.error("nim_run_turn_failed", conversation_id=conversation_id, error=str(exc))
            return AgentResponse(
                action="error",
                text="Samahani, kuna hitilafu ya kiufundi. Tafadhali jaribu tena.",
                data={"error": str(exc)},
            )

        # ── Check for terminal tool signal ────────────────────────────────────
        terminal_msg = next(
            (m for m in turn.all_messages if m.get("role") == "terminal_tool"),
            None,
        )
        if terminal_msg:
            tool_name = terminal_msg["name"]
            args = terminal_msg.get("arguments", {})

            if tool_name == "confirm_order":
                return AgentResponse(
                    action="order_confirmed",
                    text="",
                    data=args,
                    total_tokens=turn.total_tokens,
                    tool_calls_made=turn.tool_calls_made,
                )
            if tool_name == "request_quote":
                return AgentResponse(
                    action="quote_requested",
                    text="",
                    data=args,
                    total_tokens=turn.total_tokens,
                    tool_calls_made=turn.tool_calls_made,
                )
            if tool_name == "escalate_to_human":
                return AgentResponse(
                    action="escalate",
                    text="",
                    data=args,
                    total_tokens=turn.total_tokens,
                    tool_calls_made=turn.tool_calls_made,
                )

        # ── Regular text reply ─────────────────────────────────────────────────
        reply_text = turn.final_text or "Samahani, sijaweza kuelewa. Tafadhali rudia."
        try:
            await save_assistant_reply(conversation_id, reply_text, session)
        except Exception as exc:
            logger.warning("save_reply_failed", conversation_id=conversation_id, error=str(exc))

        return AgentResponse(
            action="reply",
            text=reply_text,
            total_tokens=turn.total_tokens,
            tool_calls_made=turn.tool_calls_made,
        )
