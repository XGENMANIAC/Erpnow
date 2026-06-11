"""
Tests for NimClient — the OpenAI-compatible LLM client with tool-calling loop.

All HTTP calls are intercepted by respx; no live NIM endpoint needed.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.agents.nim_client import AgentTurn, NimClient, NimError, ToolCall

NIM_BASE = "https://nim.test/v1"
NIM_KEY = "test-nim-key"
MODEL = "test-model"


def _make_client(**kwargs) -> NimClient:
    return NimClient(api_key=NIM_KEY, base_url=NIM_BASE, model=MODEL, **kwargs)


def _text_response(content: str, finish_reason: str = "stop") -> httpx.Response:
    body = {
        "choices": [
            {
                "message": {"role": "assistant", "content": content, "tool_calls": None},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    return httpx.Response(200, json=body)


def _tool_call_response(
    tool_name: str,
    tool_args: dict,
    tool_id: str = "call_001",
) -> httpx.Response:
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tool_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(tool_args),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
    }
    return httpx.Response(200, json=body)


def _error_response(status: int) -> httpx.Response:
    return httpx.Response(status, json={"error": "rate limited"})


# ── Basic text response ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_turn_returns_text_reply():
    with respx.mock(base_url=NIM_BASE) as mock:
        mock.post("/chat/completions").mock(return_value=_text_response("Habari yako!"))
        async with _make_client() as client:
            result = await client.run_turn(
                messages=[{"role": "user", "content": "Habari"}]
            )
    assert isinstance(result, AgentTurn)
    assert result.final_text == "Habari yako!"
    assert result.tool_calls_made == 0
    assert result.total_tokens == 15


# ── Tool call followed by text response ───────────────────────────────────────

@pytest.mark.asyncio
async def test_run_turn_executes_tool_and_returns_text():
    tool_executor = AsyncMock(return_value='{"price": 1500}')

    responses = [
        _tool_call_response("get_price", {"item_code": "PIPE-001"}),
        _text_response("Bei ya PIPE-001 ni KES 1,500."),
    ]
    call_count = 0

    with respx.mock(base_url=NIM_BASE) as mock:
        def _side_effect(request):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        mock.post("/chat/completions").mock(side_effect=_side_effect)

        async with _make_client() as client:
            result = await client.run_turn(
                messages=[{"role": "user", "content": "Bei ya PIPE-001?"}],
                tools=[{"type": "function", "function": {"name": "get_price", "parameters": {}}}],
                tool_executor=tool_executor,
            )

    assert result.final_text == "Bei ya PIPE-001 ni KES 1,500."
    assert result.tool_calls_made == 1
    tool_executor.assert_awaited_once()
    called_tc = tool_executor.call_args[0][0]
    assert called_tc.name == "get_price"
    assert called_tc.arguments == {"item_code": "PIPE-001"}


# ── Terminal tool stops the loop ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_turn_stops_on_terminal_tool():
    terminal_names = frozenset(["confirm_order"])
    order_args = {"items": [{"item_code": "PIPE-001", "quantity": 5}]}

    with respx.mock(base_url=NIM_BASE) as mock:
        mock.post("/chat/completions").mock(
            return_value=_tool_call_response("confirm_order", order_args, "call_term")
        )

        async with _make_client() as client:
            result = await client.run_turn(
                messages=[{"role": "user", "content": "Ndiyo, confirm order"}],
                tools=[],
                terminal_tool_names=terminal_names,
            )

    assert result.final_text == ""
    terminal_msgs = [m for m in result.all_messages if m.get("role") == "terminal_tool"]
    assert len(terminal_msgs) == 1
    assert terminal_msgs[0]["name"] == "confirm_order"
    assert terminal_msgs[0]["arguments"] == order_args


# ── Tool executor error is handled gracefully ─────────────────────────────────

@pytest.mark.asyncio
async def test_run_turn_tool_executor_error_continues():
    failing_executor = AsyncMock(side_effect=RuntimeError("ERP down"))

    responses = [
        _tool_call_response("check_stock", {"item_code": "PIPE-001"}),
        _text_response("Samahani, hali ya hisa haijulikani sasa hivi."),
    ]
    call_count = 0

    with respx.mock(base_url=NIM_BASE) as mock:
        def _side_effect(request):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        mock.post("/chat/completions").mock(side_effect=_side_effect)

        async with _make_client() as client:
            result = await client.run_turn(
                messages=[{"role": "user", "content": "Je kuna stock?"}],
                tools=[],
                tool_executor=failing_executor,
            )

    assert "Samahani" in result.final_text
    tool_msgs = [m for m in result.all_messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "error" in json.loads(tool_msgs[0]["content"])


# ── Max tool rounds exceeded ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_turn_max_tool_rounds_exceeded():
    tool_executor = AsyncMock(return_value='{"result": "ok"}')

    with respx.mock(base_url=NIM_BASE) as mock:
        # Always return a tool call — never a text response
        mock.post("/chat/completions").mock(
            return_value=_tool_call_response("search_catalog", {"query": "pipes"})
        )

        async with _make_client() as client:
            result = await client.run_turn(
                messages=[{"role": "user", "content": "Tafuta bidhaa"}],
                tools=[],
                tool_executor=tool_executor,
                max_tool_rounds=3,
            )

    assert result.final_text == ""
    assert result.tool_calls_made == 3


# ── API error raises NimError ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_call_api_500_raises_nim_error():
    with respx.mock(base_url=NIM_BASE) as mock:
        mock.post("/chat/completions").mock(return_value=_error_response(500))

        async with _make_client() as client:
            with pytest.raises(NimError) as exc_info:
                await client._call_api([{"role": "user", "content": "test"}])

    assert exc_info.value.status_code == 500


# ── No tool executor provided returns error JSON ──────────────────────────────

@pytest.mark.asyncio
async def test_run_turn_no_tool_executor_returns_error_json():
    responses = [
        _tool_call_response("get_price", {"item_code": "X"}),
        _text_response("Sorry, could not fetch price."),
    ]
    call_count = 0

    with respx.mock(base_url=NIM_BASE) as mock:
        def _side_effect(request):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        mock.post("/chat/completions").mock(side_effect=_side_effect)

        async with _make_client() as client:
            result = await client.run_turn(
                messages=[{"role": "user", "content": "price?"}],
                tools=[],
                tool_executor=None,
            )

    tool_msgs = [m for m in result.all_messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "error" in json.loads(tool_msgs[0]["content"])


# ── Token accumulation across rounds ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_turn_accumulates_tokens():
    tool_executor = AsyncMock(return_value='{"price": 500}')

    responses = [
        _tool_call_response("get_price", {"item_code": "A"}),
        _text_response("Bei ni KES 500."),
    ]
    call_count = 0

    with respx.mock(base_url=NIM_BASE) as mock:
        def _side_effect(request):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        mock.post("/chat/completions").mock(side_effect=_side_effect)

        async with _make_client() as client:
            result = await client.run_turn(
                messages=[{"role": "user", "content": "Bei?"}],
                tools=[],
                tool_executor=tool_executor,
            )

    # 28 tokens from tool-call round + 15 tokens from text round
    assert result.total_tokens == 43


# ── Malformed tool arguments are parsed safely ────────────────────────────────

@pytest.mark.asyncio
async def test_run_turn_handles_malformed_tool_args():
    bad_json = "not json {"
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_bad",
                            "type": "function",
                            "function": {"name": "get_price", "arguments": bad_json},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
    }

    responses = [
        httpx.Response(200, json=body),
        _text_response("Samahani."),
    ]
    call_count = 0
    tool_executor = AsyncMock(return_value='{"error": "invalid args"}')

    with respx.mock(base_url=NIM_BASE) as mock:
        def _side_effect(request):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        mock.post("/chat/completions").mock(side_effect=_side_effect)

        async with _make_client() as client:
            result = await client.run_turn(
                messages=[{"role": "user", "content": "test"}],
                tools=[],
                tool_executor=tool_executor,
            )

    # Should not crash; bad args stored under _raw key
    called_tc = tool_executor.call_args[0][0]
    assert "_raw" in called_tc.arguments
