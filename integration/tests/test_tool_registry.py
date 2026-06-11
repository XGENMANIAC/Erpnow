"""
Tests for ToolRegistry and build_erp_tools factory.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.tools.registry import (
    TERMINAL_TOOL_NAMES,
    TERMINAL_TOOLS,
    ToolRegistry,
    build_erp_tools,
)


# ── ToolRegistry basics ────────────────────────────────────────────────────────

def test_register_and_list_tools():
    reg = ToolRegistry()
    reg.register("my_tool", "Does things", {"type": "object", "properties": {}}, AsyncMock())
    assert "my_tool" in reg.list_tools()


def test_to_openai_format_structure():
    reg = ToolRegistry()
    reg.register(
        "greet",
        "Says hello",
        {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        AsyncMock(),
    )
    fmt = reg.to_openai_format()
    assert len(fmt) == 1
    assert fmt[0]["type"] == "function"
    assert fmt[0]["function"]["name"] == "greet"
    assert fmt[0]["function"]["description"] == "Says hello"
    assert "properties" in fmt[0]["function"]["parameters"]


@pytest.mark.asyncio
async def test_execute_known_tool_returns_json_string():
    reg = ToolRegistry()
    mock_fn = AsyncMock(return_value={"price": 1500})
    reg.register("get_price", "Get price", {"type": "object", "properties": {}}, mock_fn)

    result = await reg.execute("get_price", {"item_code": "X"})

    assert isinstance(result, str)
    parsed = json.loads(result)
    assert parsed["price"] == 1500
    mock_fn.assert_awaited_once_with(item_code="X")


@pytest.mark.asyncio
async def test_execute_unknown_tool_returns_error_json():
    reg = ToolRegistry()
    result = await reg.execute("nonexistent", {})
    parsed = json.loads(result)
    assert "error" in parsed
    assert "nonexistent" in parsed["error"]


@pytest.mark.asyncio
async def test_execute_tool_exception_returns_error_json():
    reg = ToolRegistry()
    broken_fn = AsyncMock(side_effect=ValueError("ERP exploded"))
    reg.register("broken", "Broken tool", {"type": "object", "properties": {}}, broken_fn)

    result = await reg.execute("broken", {})
    parsed = json.loads(result)
    assert "error" in parsed
    assert "ERP exploded" in parsed["error"]


@pytest.mark.asyncio
async def test_execute_tool_returning_string_not_double_encoded():
    reg = ToolRegistry()
    raw_json_fn = AsyncMock(return_value='{"already": "json"}')
    reg.register("raw", "Already JSON", {"type": "object", "properties": {}}, raw_json_fn)

    result = await reg.execute("raw", {})
    assert result == '{"already": "json"}'


# ── build_erp_tools factory ────────────────────────────────────────────────────

def _mock_settings():
    s = MagicMock()
    s.erpnext_price_list = "Standard Selling"
    s.erpnext_default_warehouse = "Stores - DX"
    return s


def test_build_erp_tools_registers_expected_tools():
    mock_client = MagicMock()
    reg = build_erp_tools(mock_client, _mock_settings())
    tools = reg.list_tools()
    assert "get_price" in tools
    assert "check_stock" in tools
    assert "search_catalog" in tools


def test_build_erp_tools_openai_format_has_required_fields():
    mock_client = MagicMock()
    reg = build_erp_tools(mock_client, _mock_settings())
    fmt = reg.to_openai_format()
    names = {t["function"]["name"] for t in fmt}
    assert names == {"get_price", "check_stock", "search_catalog"}

    for tool in fmt:
        fn = tool["function"]
        assert "description" in fn
        assert "parameters" in fn
        assert fn["parameters"]["type"] == "object"


def test_build_erp_tools_get_price_has_required_item_code():
    mock_client = MagicMock()
    reg = build_erp_tools(mock_client, _mock_settings())
    fmt = reg.to_openai_format()
    get_price = next(t for t in fmt if t["function"]["name"] == "get_price")
    assert "item_code" in get_price["function"]["parameters"]["required"]


def test_build_erp_tools_search_catalog_has_required_query():
    mock_client = MagicMock()
    reg = build_erp_tools(mock_client, _mock_settings())
    fmt = reg.to_openai_format()
    search = next(t for t in fmt if t["function"]["name"] == "search_catalog")
    assert "query" in search["function"]["parameters"]["required"]


# ── TERMINAL_TOOLS ─────────────────────────────────────────────────────────────

def test_terminal_tool_names_contains_expected():
    assert "confirm_order" in TERMINAL_TOOL_NAMES
    assert "request_quote" in TERMINAL_TOOL_NAMES
    assert "escalate_to_human" in TERMINAL_TOOL_NAMES


def test_terminal_tools_have_valid_schema():
    for tool in TERMINAL_TOOLS:
        assert tool["type"] == "function"
        fn = tool["function"]
        assert "name" in fn
        assert "description" in fn
        assert "parameters" in fn
        assert fn["parameters"]["type"] == "object"


def test_confirm_order_requires_items():
    confirm = next(t for t in TERMINAL_TOOLS if t["function"]["name"] == "confirm_order")
    assert "items" in confirm["function"]["parameters"]["required"]


def test_escalate_to_human_requires_reason():
    escalate = next(t for t in TERMINAL_TOOLS if t["function"]["name"] == "escalate_to_human")
    assert "reason" in escalate["function"]["parameters"]["required"]


def test_terminal_tool_names_matches_terminal_tools_list():
    declared = {t["function"]["name"] for t in TERMINAL_TOOLS}
    assert declared == set(TERMINAL_TOOL_NAMES)
