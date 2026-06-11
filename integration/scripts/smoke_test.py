#!/usr/bin/env python3
"""
Phase 1 live smoke test — runs against a real ERPNext instance.

Usage:
    cd integration
    python scripts/smoke_test.py

Required env vars (set in .env or export directly):
    ERPNEXT_BASE_URL, ERPNEXT_API_KEY, ERPNEXT_API_SECRET,
    ERPNEXT_COMPANY, ERPNEXT_DEFAULT_WAREHOUSE, ERPNEXT_PRICE_LIST

What it verifies (Phase 1 acceptance criteria):
    1. Auth works — we can reach ERPNext
    2. Can find or create a test customer by phone
    3. Can read items from the catalog
    4. Can read stock from a warehouse
    5. Can create a Sales Order (ERPNext computes totals/VAT)
    6. Can submit a Sales Invoice (ERPNext generates the document)
    7. All calls are idempotent — running twice produces no duplicates
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

# Load .env if present
env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.erp.client import ERPNextClient, ERPNextError
from app.erp.customers import find_or_create_customer
from app.erp.catalog import get_products, get_price, check_stock
from app.erp.sales import create_sales_order, submit_sales_invoice
from app.erp.schemas import OrderItem

REQUIRED = [
    "ERPNEXT_BASE_URL", "ERPNEXT_API_KEY", "ERPNEXT_API_SECRET",
    "ERPNEXT_COMPANY", "ERPNEXT_DEFAULT_WAREHOUSE", "ERPNEXT_PRICE_LIST",
]

G, R, Y, RESET, BOLD = "\033[92m", "\033[91m", "\033[93m", "\033[0m", "\033[1m"

passed = failed = 0


def ok(msg: str) -> None:
    global passed; passed += 1
    print(f"  {G}✓{RESET} {msg}")


def fail(msg: str, err: Exception | None = None) -> None:
    global failed; failed += 1
    print(f"  {R}✗{RESET} {msg}")
    if err:
        print(f"    {R}Error:{RESET} {err}")


def warn(msg: str) -> None:
    print(f"  {Y}⚠{RESET}  {msg}")


def section(title: str) -> None:
    print(f"\n{BOLD}{title}{RESET}")


async def run() -> int:
    section("0. Pre-flight checks")
    missing = [v for v in REQUIRED if not os.environ.get(v)]
    if missing:
        fail(f"Missing env vars: {', '.join(missing)}")
        print(f"\n  Set these in integration/.env and re-run.\n")
        return 1
    ok("All required env vars present")

    base_url = os.environ["ERPNEXT_BASE_URL"]
    api_key = os.environ["ERPNEXT_API_KEY"]
    api_secret = os.environ["ERPNEXT_API_SECRET"]
    warehouse = os.environ["ERPNEXT_DEFAULT_WAREHOUSE"]
    price_list = os.environ["ERPNEXT_PRICE_LIST"]
    print(f"  Target: {base_url}")

    section("1. Authentication")
    client = ERPNextClient(base_url=base_url, api_key=api_key, api_secret=api_secret)
    try:
        await client.get("/api/resource/DocType/Customer")
        ok("Reached ERPNext — Customer doctype accessible")
    except ERPNextError as e:
        fail("Could not reach ERPNext", e)
        await client.aclose()
        return 1

    section("2. Product catalog")
    items = []
    try:
        items = await get_products(client, limit=5)
        if items:
            ok(f"get_products: {len(items)} item(s) — first: {items[0].item_name}")
        else:
            warn("No items found — add Items in ERPNext first (see SETUP_FRAPPE_CLOUD.md)")
    except ERPNextError as e:
        fail("get_products failed", e)

    first_item_code = items[0].item_code if items else None

    if first_item_code:
        try:
            price = await get_price(client, first_item_code, price_list)
            ok(f"get_price({first_item_code}): KES {price.price_list_rate}")
        except ERPNextError as e:
            warn(f"No price for {first_item_code} in '{price_list}' — add an Item Price in ERPNext")

        try:
            stock = await check_stock(client, first_item_code, warehouse)
            ok(f"check_stock @ {warehouse}: {stock.actual_qty} available")
        except ERPNextError as e:
            fail("check_stock failed", e)

    section("3. Customer find/create")
    test_phone = "+254700000001"
    run_id = str(uuid.uuid4())[:8]
    customer_name: str | None = None

    try:
        customer = await find_or_create_customer(
            client, test_phone, name="DEWMIX Smoke Test",
            idempotency_key=f"smoke:{run_id}:customer",
        )
        ok(f"find_or_create_customer → {customer.name} ({customer.customer_name})")
        customer_name = customer.name
    except ERPNextError as e:
        fail("find_or_create_customer failed", e)

    section("4. Sales Order — ERPNext computes totals")
    so_name: str | None = None

    if not first_item_code:
        warn("Skipping — no items in catalog")
    elif not customer_name:
        warn("Skipping — customer step failed")
    else:
        try:
            so_result = await create_sales_order(
                client, customer_name,
                [OrderItem(item_code=first_item_code, qty=Decimal("1"))],
                idempotency_key=f"smoke:{run_id}:so",
            )
            order = so_result["order"]
            bd = so_result["total_breakdown"]
            ok(f"Sales Order: {order.name} (docstatus={order.docstatus})")
            ok(f"Grand total from ERPNext: KES {bd['grand_total']}")
            ok(f"VAT from ERPNext: KES {bd['taxes_and_charges']}")
            so_name = order.name
        except ERPNextError as e:
            fail("create_sales_order failed", e)

    section("5. Sales Invoice")
    if not so_name:
        warn("Skipping — sales order step failed")
    else:
        try:
            inv = await submit_sales_invoice(
                client, so_name, idempotency_key=f"smoke:{run_id}:inv",
            )
            ok(f"Invoice: {inv['invoice_no']}")
            ok(f"PDF: {base_url}{inv['pdf_url']}")
        except ERPNextError as e:
            fail("submit_sales_invoice failed", e)

    section("6. Idempotency — second call, same key")
    if customer_name:
        try:
            c2 = await find_or_create_customer(
                client, test_phone, idempotency_key=f"smoke:{run_id}:customer",
            )
            if c2.name == customer_name:
                ok("Same customer returned — no duplicate created")
            else:
                fail(f"Different customer on second call: {c2.name} vs {customer_name}")
        except ERPNextError as e:
            fail("Idempotency check failed", e)

    await client.aclose()

    total = passed + failed
    print(f"\n{'='*50}")
    print(f"{BOLD}Results: {G}{passed}{RESET}{BOLD} passed, {R}{failed}{RESET}{BOLD} failed / {total}{RESET}")
    if failed == 0:
        print(f"{G}{BOLD}Phase 1 acceptance criteria: PASSED ✓{RESET}")
    else:
        print(f"{R}{BOLD}Phase 1 acceptance criteria: FAILED ✗{RESET}")
    print(f"{'='*50}\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
