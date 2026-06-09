"""
Read-only catalog operations: items, prices, and stock.

All totals, taxes, and pricing rules are fetched from ERPNext — never
computed here.
"""
from __future__ import annotations

import json
from decimal import Decimal

import structlog

from app.erp.client import ERPNextClient, ERPNextNotFoundError
from app.erp.schemas import ERPItem, ERPItemPrice, ERPStockBin

logger = structlog.get_logger(__name__)


async def get_products(
    client: ERPNextClient,
    query: str | None = None,
    limit: int = 20,
) -> list[ERPItem]:
    """
    List active, sellable items.  Optionally filter by name/description.
    """
    filters_list: list[list[str]] = [
        ["disabled", "=", "0"],
        ["is_sales_item", "=", "1"],
    ]
    if query:
        filters_list.append(["item_name", "like", f"%{query}%"])

    fields = json.dumps(["name", "item_name", "item_code", "description",
                          "stock_uom", "is_stock_item"])
    results = await client.get(
        "/api/resource/Item",
        params={
            "filters": json.dumps(filters_list),
            "fields": fields,
            "limit_page_length": limit,
        },
    )
    items = results if isinstance(results, list) else []
    logger.debug("catalog_items_fetched", count=len(items), query=query)
    return [ERPItem(**i) for i in items]


async def get_price(
    client: ERPNextClient,
    item_code: str,
    price_list: str,
    customer: str | None = None,
    qty: Decimal = Decimal("1"),
) -> ERPItemPrice:
    """
    Fetch the selling price for an item from ERPNext.

    Uses the get_item_details server method so pricing rules and
    customer-specific prices are applied by ERPNext — never computed here.
    """
    # Primary: query Item Price doctype directly (most reliable)
    filters = json.dumps([
        ["item_code", "=", item_code],
        ["price_list", "=", price_list],
        ["selling", "=", 1],
    ])
    fields = json.dumps(["item_code", "price_list", "currency", "price_list_rate"])
    prices = await client.get(
        "/api/resource/Item Price",
        params={"filters": filters, "fields": fields, "limit_page_length": 1},
    )
    if isinstance(prices, list) and prices:
        p = prices[0]
        return ERPItemPrice(
            item_code=p["item_code"],
            price_list=p["price_list"],
            currency=p.get("currency", "KES"),
            price_list_rate=Decimal(str(p["price_list_rate"])),
        )

    # Fallback: use frappe.client.get_value server method
    fallback = await client.post(
        "/api/method/frappe.client.get_value",
        json={
            "doctype": "Item Price",
            "filters": {"item_code": item_code, "price_list": price_list},
            "fieldname": ["price_list_rate", "currency"],
        },
    )
    # Server method response may be wrapped or be the dict directly
    if isinstance(fallback, dict) and fallback.get("message"):
        fallback = fallback["message"]
    if isinstance(fallback, dict) and fallback.get("price_list_rate"):
        return ERPItemPrice(
            item_code=item_code,
            price_list=price_list,
            currency=fallback.get("currency", "KES"),
            price_list_rate=Decimal(str(fallback["price_list_rate"])),
        )

    raise ERPNextNotFoundError(
        f"No price found for item '{item_code}' in price list '{price_list}'"
    )


async def check_stock(
    client: ERPNextClient,
    item_code: str,
    warehouse: str | None = None,
) -> ERPStockBin:
    """
    Return available stock from the Bin (warehouse stock ledger summary).
    """
    filters_list: list[list[str]] = [["item_code", "=", item_code]]
    if warehouse:
        filters_list.append(["warehouse", "=", warehouse])

    fields = json.dumps(["item_code", "warehouse", "actual_qty",
                          "reserved_qty", "projected_qty"])
    results = await client.get(
        "/api/resource/Bin",
        params={
            "filters": json.dumps(filters_list),
            "fields": fields,
            "limit_page_length": 1,
        },
    )
    if isinstance(results, list) and results:
        b = results[0]
        return ERPStockBin(
            item_code=b["item_code"],
            warehouse=b["warehouse"],
            actual_qty=Decimal(str(b.get("actual_qty", 0))),
            reserved_qty=Decimal(str(b.get("reserved_qty", 0))),
            projected_qty=Decimal(str(b.get("projected_qty", 0))),
        )

    # No bin row means zero stock
    return ERPStockBin(
        item_code=item_code,
        warehouse=warehouse or "",
        actual_qty=Decimal("0"),
        reserved_qty=Decimal("0"),
        projected_qty=Decimal("0"),
    )
