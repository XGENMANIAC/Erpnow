"""
Tool registry — typed tool definitions + executor for the LLM tool-calling loop.

Each tool has:
  - A JSON schema (for the LLM tool list)
  - An async callable (for execution)

The ERP tools are created via build_erp_tools() which binds them to a live
ERPNextClient and settings.  Tests can build a registry with mock callables.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict  # JSON Schema "object" type
    fn: Callable


class ToolRegistry:
    """Typed registry of async tool callables with their JSON-Schema definitions."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict,
        fn: Callable,
    ) -> None:
        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            fn=fn,
        )

    def to_openai_format(self) -> list[dict]:
        """Return tool definitions in the format the LLM expects."""
        return [
            {
                "type": "function",
                "function": {
                    "name": td.name,
                    "description": td.description,
                    "parameters": td.parameters,
                },
            }
            for td in self._tools.values()
        ]

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        """
        Execute a registered tool and return the result as a JSON string.
        Returns an error JSON string if the tool raises or is unknown.
        """
        if name not in self._tools:
            return json.dumps({"error": f"Unknown tool: {name!r}"})
        try:
            result = await self._tools[name].fn(**arguments)
            return json.dumps(result) if not isinstance(result, str) else result
        except Exception as exc:
            logger.warning("tool_execution_error", tool=name, error=str(exc))
            return json.dumps({"error": str(exc)})

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())


# ── ERP tool factories ────────────────────────────────────────────────────────

def build_erp_tools(client: Any, settings: Any) -> ToolRegistry:
    """
    Return a ToolRegistry populated with ERP read tools for the conversation
    agent.  Bound to the given ERPNextClient and settings.

    Only read tools are included here (get_price, check_stock, search_catalog).
    Mutating operations (create_order, invoice, payment) are performed
    directly by the Orchestrator, not via the LLM tool loop.
    """
    from app.erp.catalog import check_stock, get_price, get_products

    registry = ToolRegistry()

    # ── get_price ─────────────────────────────────────────────────────────────
    async def _get_price(item_code: str, price_list: str | None = None) -> dict:
        pl = price_list or settings.erpnext_price_list
        item_price = await get_price(client, item_code, price_list=pl)
        return {
            "item_code": item_price.item_code,
            "price_list_rate": str(item_price.price_list_rate),
            "currency": item_price.currency,
            "price_list": item_price.price_list,
        }

    registry.register(
        name="get_price",
        description=(
            "Get the current selling price for a product from ERPNext. "
            "Always call this before quoting a price — never quote from memory."
        ),
        parameters={
            "type": "object",
            "properties": {
                "item_code": {
                    "type": "string",
                    "description": "ERPNext item code (e.g. 'TEST-PIPE-001')",
                },
                "price_list": {
                    "type": "string",
                    "description": "Price list name (default: Standard Selling)",
                },
            },
            "required": ["item_code"],
        },
        fn=_get_price,
    )

    # ── check_stock ───────────────────────────────────────────────────────────
    async def _check_stock(item_code: str, warehouse: str | None = None) -> dict:
        wh = warehouse or settings.erpnext_default_warehouse
        bin_ = await check_stock(client, item_code, warehouse=wh)
        return {
            "item_code": bin_.item_code,
            "warehouse": bin_.warehouse,
            "available_qty": str(bin_.actual_qty),
            "reserved_qty": str(bin_.reserved_qty),
        }

    registry.register(
        name="check_stock",
        description=(
            "Check available stock for an item at a warehouse. "
            "Always call this before confirming stock availability."
        ),
        parameters={
            "type": "object",
            "properties": {
                "item_code": {"type": "string"},
                "warehouse": {
                    "type": "string",
                    "description": "Warehouse name (default: Stores - DX)",
                },
            },
            "required": ["item_code"],
        },
        fn=_check_stock,
    )

    # ── search_catalog ────────────────────────────────────────────────────────
    async def _search_catalog(query: str, item_group: str | None = None) -> dict:
        items = await get_products(client, item_group=item_group, search=query, limit=5)
        return {
            "items": [
                {
                    "item_code": it.item_code,
                    "item_name": it.item_name,
                    "description": it.description,
                    "stock_uom": it.stock_uom,
                }
                for it in items
            ]
        }

    registry.register(
        name="search_catalog",
        description=(
            "Search the product catalog by name, keyword, or category. "
            "Use this to find item codes when the customer describes a product."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Product name or keyword, e.g. 'PVC pipe 1 inch'",
                },
                "item_group": {
                    "type": "string",
                    "description": "Product category filter (optional)",
                },
            },
            "required": ["query"],
        },
        fn=_search_catalog,
    )

    return registry


# ── Terminal signal tools (no execution — intercepted by the agent loop) ──────

TERMINAL_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "confirm_order",
            "description": (
                "Call this ONLY when the customer has explicitly said yes/ndiyo/sawa "
                "and confirmed they want to place the order with the exact items discussed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item_code": {"type": "string"},
                                "quantity": {"type": "number"},
                            },
                            "required": ["item_code", "quantity"],
                        },
                        "description": "Items the customer confirmed",
                    },
                    "customer_name": {
                        "type": "string",
                        "description": "Customer's name if provided",
                    },
                    "delivery_preference": {
                        "type": "string",
                        "enum": ["pickup", "delivery"],
                        "description": "pickup at shop or delivery",
                    },
                    "delivery_address": {
                        "type": "string",
                        "description": "Delivery address if delivery_preference is delivery",
                    },
                },
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_quote",
            "description": "Call this when the customer wants a price quote but not to place an order yet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item_code": {"type": "string"},
                                "quantity": {"type": "number"},
                            },
                            "required": ["item_code", "quantity"],
                        },
                    },
                },
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": (
                "Call this when: the customer is frustrated, the order is very high value "
                "(over KES 50,000), you are unsure about a spec, or the customer asks about "
                "credit terms, bulk discounts, or delivery outside Nairobi/Nyeri."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Brief reason for escalation",
                    },
                },
                "required": ["reason"],
            },
        },
    },
]

TERMINAL_TOOL_NAMES: frozenset[str] = frozenset(
    t["function"]["name"] for t in TERMINAL_TOOLS
)

# Module-level singleton (populated by build_erp_tools at startup)
registry = ToolRegistry()
