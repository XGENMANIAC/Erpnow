"""
Customer operations against ERPNext.

Customers are identified by phone number.  We store the normalized phone in
both `mobile_no` (standard field) and `custom_phone` (custom field added via
the frappe-app) so lookups are reliable even before the custom field is added.
"""
from __future__ import annotations

import json
import re

import structlog

from app.erp.client import ERPNextClient, ERPNextNotFoundError
from app.erp.schemas import ERPCustomer

logger = structlog.get_logger(__name__)


def _normalize_phone(phone: str) -> str:
    """Return phone in +254XXXXXXXXX format."""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif digits.startswith("254") and len(digits) == 12:
        pass
    # Return with + prefix
    return "+" + digits if not digits.startswith("+") else digits


async def find_or_create_customer(
    client: ERPNextClient,
    phone: str,
    name: str | None = None,
    idempotency_key: str | None = None,
) -> ERPCustomer:
    """
    Look up a Customer by phone number; create one if not found.

    Phone is normalized to +254XXXXXXXXX before searching or storing.
    The idempotency_key is forwarded to the POST call so concurrent
    requests for the same new customer collapse safely.
    """
    normalized = _normalize_phone(phone)

    # 1. Try searching by custom_phone first
    filters = json.dumps([["custom_phone", "=", normalized]])
    fields = json.dumps(["name", "customer_name", "mobile_no", "custom_phone",
                         "customer_type", "customer_group", "territory"])
    results = await client.get(
        "/api/resource/Customer",
        params={"filters": filters, "fields": fields, "limit_page_length": 1},
    )

    if isinstance(results, list) and results:
        doc = results[0]
        logger.info("customer_found", phone=normalized, customer=doc["name"])
        return ERPCustomer(**doc)

    # 2. Fallback: search by mobile_no
    filters2 = json.dumps([["mobile_no", "=", normalized]])
    results2 = await client.get(
        "/api/resource/Customer",
        params={"filters": filters2, "fields": fields, "limit_page_length": 1},
    )
    if isinstance(results2, list) and results2:
        doc = results2[0]
        logger.info("customer_found_by_mobile", phone=normalized, customer=doc["name"])
        return ERPCustomer(**doc)

    # 3. Not found — create
    customer_name = name or f"Customer {normalized}"
    payload = {
        "doctype": "Customer",
        "customer_name": customer_name,
        "customer_type": "Individual",
        "customer_group": "All Customer Groups",
        "territory": "Kenya",
        "mobile_no": normalized,
        "custom_phone": normalized,
    }
    created = await client.post(
        "/api/resource/Customer",
        json=payload,
        idempotency_key=idempotency_key or normalized,
    )
    logger.info("customer_created", phone=normalized, customer=created.get("name"))
    return ERPCustomer(**created)
