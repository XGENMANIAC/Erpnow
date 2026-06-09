"""
Tests for app/erp/catalog.py

Covers:
  - get_products: no query (all items)
  - get_products: with query (filtered items)
  - get_price: returns Decimal (not float)
  - get_price: falls back when Item Price list is empty
  - check_stock: returns ERPStockBin with correct Decimals
  - check_stock: returns zero-qty bin when no stock record found
"""
from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
import respx

from app.erp.catalog import check_stock, get_price, get_products
from app.erp.client import ERPNextClient, ERPNextNotFoundError
from app.erp.schemas import ERPItem, ERPItemPrice, ERPStockBin
from tests.conftest import (
    BASE_URL,
    API_KEY,
    API_SECRET,
    ITEM_DATA,
    ITEM_PRICE_DATA,
    STOCK_BIN_DATA,
)


def make_client() -> ERPNextClient:
    return ERPNextClient(base_url=BASE_URL, api_key=API_KEY, api_secret=API_SECRET)


# ── get_products ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_products_returns_list():
    """get_products returns a list of ERPItem instances."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/resource/Item").mock(
            return_value=httpx.Response(200, json={"data": [ITEM_DATA]})
        )
        client = make_client()
        products = await get_products(client)

    assert len(products) == 1
    assert isinstance(products[0], ERPItem)
    assert products[0].item_code == "ITEM-001"


@pytest.mark.asyncio
async def test_get_products_with_query_sends_filter():
    """When query is provided, filter is included in the request."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/resource/Item").mock(
            return_value=httpx.Response(200, json={"data": [ITEM_DATA]})
        )
        client = make_client()
        products = await get_products(client, query="Widget")

        assert len(products) == 1
        # Verify filter was sent in params — must be inside the with block
        request = mock.calls.last.request
        params = dict(httpx.URL(str(request.url)).params)
        assert "Widget" in params.get("filters", "")


@pytest.mark.asyncio
async def test_get_products_empty_returns_empty_list():
    """Empty response returns empty list."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/resource/Item").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        client = make_client()
        products = await get_products(client)

    assert products == []


@pytest.mark.asyncio
async def test_get_products_respects_limit():
    """Limit parameter is forwarded to ERPNext."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/resource/Item").mock(
            return_value=httpx.Response(200, json={"data": [ITEM_DATA]})
        )
        client = make_client()
        await get_products(client, limit=5)

        request = mock.calls.last.request
        params = dict(httpx.URL(str(request.url)).params)
        assert params.get("limit_page_length") == "5"


# ── get_price ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_price_returns_decimal():
    """price_list_rate must be a Decimal, not a float."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/resource/Item Price").mock(
            return_value=httpx.Response(200, json={"data": [ITEM_PRICE_DATA]})
        )
        client = make_client()
        price = await get_price(client, "ITEM-001", "Standard Selling")

    assert isinstance(price, ERPItemPrice)
    assert isinstance(price.price_list_rate, Decimal)
    assert price.price_list_rate == Decimal("1500.00")
    assert price.currency == "KES"


@pytest.mark.asyncio
async def test_get_price_fallback_to_method():
    """When Item Price list is empty, falls back to frappe.client.get_value."""
    with respx.mock(base_url=BASE_URL) as mock:
        # Item Price returns empty
        mock.get("/api/resource/Item Price").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        # Fallback method returns price
        mock.post("/api/method/frappe.client.get_value").mock(
            return_value=httpx.Response(
                200,
                json={"data": {"price_list_rate": "1200.00", "currency": "KES"}},
            )
        )
        client = make_client()
        price = await get_price(client, "ITEM-001", "Standard Selling")

    assert isinstance(price.price_list_rate, Decimal)
    assert price.price_list_rate == Decimal("1200.00")


@pytest.mark.asyncio
async def test_get_price_raises_not_found_when_no_price():
    """Raises ERPNextNotFoundError when no price exists and fallback also fails."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/resource/Item Price").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        # Fallback returns empty dict (no price_list_rate)
        mock.post("/api/method/frappe.client.get_value").mock(
            return_value=httpx.Response(200, json={"data": {}})
        )
        client = make_client()
        with pytest.raises(ERPNextNotFoundError):
            await get_price(client, "ITEM-999", "Standard Selling")


# ── check_stock ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_stock_returns_bin():
    """check_stock returns ERPStockBin with Decimal qtys."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/resource/Bin").mock(
            return_value=httpx.Response(200, json={"data": [STOCK_BIN_DATA]})
        )
        client = make_client()
        bin_ = await check_stock(client, "ITEM-001")

    assert isinstance(bin_, ERPStockBin)
    assert isinstance(bin_.actual_qty, Decimal)
    assert bin_.actual_qty == Decimal("50.00")
    assert bin_.reserved_qty == Decimal("5.00")
    assert bin_.projected_qty == Decimal("45.00")
    assert bin_.warehouse == "Stores - MC"


@pytest.mark.asyncio
async def test_check_stock_zero_when_no_bin():
    """When no bin row exists, return a zero-qty bin instead of raising."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/resource/Bin").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        client = make_client()
        bin_ = await check_stock(client, "ITEM-999")

    assert bin_.actual_qty == Decimal("0")
    assert bin_.reserved_qty == Decimal("0")
    assert bin_.projected_qty == Decimal("0")
    assert bin_.item_code == "ITEM-999"


@pytest.mark.asyncio
async def test_check_stock_with_warehouse_filter():
    """check_stock with warehouse sends warehouse filter to ERPNext."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/resource/Bin").mock(
            return_value=httpx.Response(200, json={"data": [STOCK_BIN_DATA]})
        )
        client = make_client()
        bin_ = await check_stock(client, "ITEM-001", warehouse="Stores - MC")

        request = mock.calls.last.request
        params = dict(httpx.URL(str(request.url)).params)
        assert "Stores - MC" in params.get("filters", "")
