"""
NVIDIA NIM / OpenAI-compatible chat completions client.

Implements the tool-calling loop: sends the request, executes any tool
calls the LLM returns, feeds results back, and repeats until the model
produces a final text response or a terminal tool call is made.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger(__name__)


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class CompletionResult:
    content: str | None           # final text (None when finish_reason=tool_calls)
    tool_calls: list[ToolCall]    # populated when finish_reason=tool_calls
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    messages: list[dict] = field(default_factory=list)  # full conversation after this round


@dataclass
class AgentTurn:
    """Result of a complete agent turn (may span multiple NIM calls)."""
    final_text: str
    all_messages: list[dict]       # full message history including tool exchanges
    total_tokens: int
    tool_calls_made: int = 0


# ── Errors ────────────────────────────────────────────────────────────────────

class NimError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, NimError) and exc.status_code in (429, 500, 502, 503, 504)


# ── Client ────────────────────────────────────────────────────────────────────

class NimClient:
    """
    Async OpenAI-compatible client for NVIDIA NIM (and any OpenAI-format API).

    Retries on 5xx/429.  Tool execution is injected via `tool_executor` so the
    client itself has no dependency on ERP code.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> NimClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    # ── Single API call ───────────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def _call_api(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> CompletionResult:
        """Single round-trip to the completions endpoint."""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        t0 = time.monotonic()
        resp = await self._http.post("/chat/completions", json=payload)
        duration_ms = round((time.monotonic() - t0) * 1000)

        if resp.status_code not in (200, 201):
            raise NimError(
                f"NIM API error {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
            )

        body = resp.json()
        choice = body["choices"][0]
        msg = choice["message"]
        usage = body.get("usage", {})

        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            args_raw = tc["function"].get("arguments", "{}")
            try:
                args = json.loads(args_raw)
            except json.JSONDecodeError:
                args = {"_raw": args_raw}
            tool_calls.append(ToolCall(id=tc["id"], name=tc["function"]["name"], arguments=args))

        logger.debug(
            "nim_api_call",
            model=self._model,
            finish_reason=choice["finish_reason"],
            duration_ms=duration_ms,
            total_tokens=usage.get("total_tokens", 0),
        )

        return CompletionResult(
            content=msg.get("content"),
            tool_calls=tool_calls,
            finish_reason=choice["finish_reason"],
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        )

    # ── Tool-calling loop ─────────────────────────────────────────────────────

    async def run_turn(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_executor: Callable[[ToolCall], Awaitable[str]] | None = None,
        max_tool_rounds: int = 6,
        terminal_tool_names: frozenset[str] = frozenset(),
    ) -> AgentTurn:
        """
        Run a full agent turn: call the LLM, execute any tool calls, repeat.

        Stops when:
          - The LLM generates text content (finish_reason != "tool_calls").
          - A terminal tool is called (e.g. confirm_order, escalate_to_human).
          - max_tool_rounds is exceeded.

        Returns AgentTurn with the final text and the full augmented message list.
        Terminal tool calls are appended as a sentinel to all_messages with
        role "terminal_tool" for the caller to inspect.
        """
        msgs = list(messages)
        total_tokens = 0
        tool_calls_made = 0

        for _round in range(max_tool_rounds):
            result = await self._call_api(msgs, tools)
            total_tokens += result.total_tokens

            # Append the assistant message (with or without tool_calls)
            assistant_msg: dict[str, Any] = {"role": "assistant"}
            if result.content is not None:
                assistant_msg["content"] = result.content
            if result.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in result.tool_calls
                ]
            msgs.append(assistant_msg)

            if result.finish_reason != "tool_calls" or not result.tool_calls:
                # Regular text reply
                return AgentTurn(
                    final_text=result.content or "",
                    all_messages=msgs,
                    total_tokens=total_tokens,
                    tool_calls_made=tool_calls_made,
                )

            # Execute tool calls
            for tc in result.tool_calls:
                tool_calls_made += 1

                # Terminal tool — stop the loop and signal the caller
                if tc.name in terminal_tool_names:
                    msgs.append(
                        {"role": "terminal_tool", "name": tc.name, "arguments": tc.arguments}
                    )
                    return AgentTurn(
                        final_text="",
                        all_messages=msgs,
                        total_tokens=total_tokens,
                        tool_calls_made=tool_calls_made,
                    )

                if tool_executor:
                    try:
                        tool_result = await tool_executor(tc)
                    except Exception as exc:
                        tool_result = json.dumps({"error": str(exc)})
                else:
                    tool_result = json.dumps({"error": "No tool executor provided"})

                msgs.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": tool_result}
                )

        # Max rounds exceeded
        logger.warning("nim_max_tool_rounds_exceeded", rounds=max_tool_rounds)
        return AgentTurn(
            final_text="",
            all_messages=msgs,
            total_tokens=total_tokens,
            tool_calls_made=tool_calls_made,
        )
