"""
Sales operations against ERPNext.

Covers the full selling flow:
  Quotation → Sales Order (submitted) → Sales Invoice (submitted)

Rules:
  - All totals, taxes, and VAT are computed by ERPNext — never here.
  - Every mutating call receives and forwards an idempotency_key.
  - Documents are submitted (docstatus=1) immediately after creation
    unless they are Quotations (which stay as drafts for the customer).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import structlog

from app.erp.client import ERPNextClient, ERPNextDuplicateError
from app.erp.schemas import ERPQuotation, ERPSalesInvoice, ERPSalesOrder, OrderItem

logger = structlog.get_logger(__name__)


def _items_payload(items: list[OrderItem]) -> list[dict]:
    """Convert OrderItem list to the child-table rows ERPNext expects."""
    rows = []
    for item in items:
        row: dict = {"item_code": item.item_code, "qty": str(item.qty)}
        if item.rate is not None:
            row["rate"] = str(item.rate)
        rows.append(row)
    return rows


async def create_quotation(
    client: ERPNextClient,
    customer: str,
    items: list[OrderItem],
    idempotency_key: str,
) -> dict:
    """
    Create a draft Quotation in ERPNext.

    Returns a dict with:
      - "quotation": ERPQuotation
      - "total": Decimal (grand_total from ERPNext)
      - "summary_text": human-readable line items summary
    """
    payload = {
        "doctype": "Quotation",
        "quotation_to": "Customer",
        "party_name": customer,
        "items": _items_payload(items),
        "order_type": "Sales",
    }

    try:
        data = await client.post(
            "/api/resource/Quotation",
            json=payload,
            idempotency_key=idempotency_key,
        )
    except ERPNextDuplicateError:
        logger.warning("quotation_duplicate", idempotency_key=idempotency_key)
        # Fetch the existing quotation by searching for the idempotency key
        raise

    quotation = ERPQuotation(**data)
    grand_total = quotation.grand_total

    # Build a human-readable summary from the items
    lines = []
    for item_data in quotation.items:
        code = item_data.get("item_code", "")
        qty = item_data.get("qty", "")
        rate = item_data.get("rate", "")
        amount = item_data.get("amount", "")
        lines.append(f"  {code} x{qty} @ {rate} = {amount}")

    summary_text = f"Quotation {quotation.name}\nTotal: KES {grand_total}\n" + "\n".join(lines)

    logger.info("quotation_created", name=quotation.name, total=str(grand_total))
    return {
        "quotation": quotation,
        "total": grand_total,
        "summary_text": summary_text,
    }


async def create_sales_order(
    client: ERPNextClient,
    customer: str,
    items: list[OrderItem],
    idempotency_key: str,
    delivery_date: date | None = None,
) -> dict:
    """
    Create and submit a Sales Order in ERPNext.

    The order is automatically submitted (docstatus=1) so it can be
    invoiced.  ERPNext computes all totals and taxes.

    Returns a dict with:
      - "order": ERPSalesOrder (docstatus=1)
      - "total_breakdown": dict with grand_total and taxes (from ERPNext)
    """
    if delivery_date is None:
        delivery_date = date.today() + timedelta(days=7)

    payload = {
        "doctype": "Sales Order",
        "customer": customer,
        "delivery_date": delivery_date.isoformat(),
        "items": _items_payload(items),
        "order_type": "Sales",
    }

    # Step 1: Create draft
    try:
        data = await client.post(
            "/api/resource/Sales Order",
            json=payload,
            idempotency_key=idempotency_key,
        )
    except ERPNextDuplicateError:
        logger.warning("sales_order_duplicate", idempotency_key=idempotency_key)
        raise

    order = ERPSalesOrder(**data)
    logger.info("sales_order_created_draft", name=order.name)

    # Step 2: Submit (docstatus 0 → 1)
    submitted_data = await client.put(
        f"/api/resource/Sales Order/{order.name}",
        json={"docstatus": 1},
        idempotency_key=f"{idempotency_key}:submit",
    )
    submitted_order = ERPSalesOrder(**submitted_data)
    logger.info(
        "sales_order_submitted",
        name=submitted_order.name,
        grand_total=str(submitted_order.grand_total),
    )

    total_breakdown = {
        "grand_total": submitted_order.grand_total,
        "taxes_and_charges": submitted_order.total_taxes_and_charges,
        "net_total": submitted_order.grand_total - submitted_order.total_taxes_and_charges,
        "currency": "KES",
    }

    return {"order": submitted_order, "total_breakdown": total_breakdown}


async def submit_sales_invoice(
    client: ERPNextClient,
    sales_order_name: str,
    idempotency_key: str,
) -> dict:
    """
    Create a Sales Invoice from a submitted Sales Order, then submit it.

    ERPNext handles all tax and total calculations when creating the invoice
    from the order.

    Returns a dict with:
      - "invoice_no": str
      - "pdf_url": str (ERPNext print endpoint URL)
      - "summary_text": str
    """
    # Step 1: Create invoice from Sales Order using the make_sales_invoice method
    invoice_data = await client.post(
        "/api/method/erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice",
        json={"source_name": sales_order_name},
        idempotency_key=idempotency_key,
    )

    # If ERPNext returns the doc directly under "message" key (common for server methods)
    if "message" in invoice_data:
        invoice_data = invoice_data["message"]

    # Step 2: Save the invoice (POST to persist it as a draft first)
    try:
        saved_data = await client.post(
            "/api/resource/Sales Invoice",
            json={**invoice_data, "doctype": "Sales Invoice"},
            idempotency_key=f"{idempotency_key}:save",
        )
    except ERPNextDuplicateError:
        logger.warning("invoice_duplicate", sales_order=sales_order_name)
        raise

    invoice = ERPSalesInvoice(**saved_data)

    # Step 3: Submit the invoice
    submitted_data = await client.put(
        f"/api/resource/Sales Invoice/{invoice.name}",
        json={"docstatus": 1},
        idempotency_key=f"{idempotency_key}:submit",
    )
    submitted_invoice = ERPSalesInvoice(**submitted_data)
    logger.info(
        "sales_invoice_submitted",
        name=submitted_invoice.name,
        grand_total=str(submitted_invoice.grand_total),
    )

    pdf_url = (
        f"/api/method/frappe.utils.print_format.download_pdf"
        f"?doctype=Sales+Invoice&name={submitted_invoice.name}&format=Standard"
    )

    summary_text = (
        f"Invoice {submitted_invoice.name} for Sales Order {sales_order_name}\n"
        f"Total: KES {submitted_invoice.grand_total}\n"
        f"Outstanding: KES {submitted_invoice.outstanding_amount}"
    )

    return {
        "invoice_no": submitted_invoice.name,
        "pdf_url": pdf_url,
        "summary_text": summary_text,
    }
