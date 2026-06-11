"""
Orchestrator — pure Python ERP execution for confirmed orders.

Rules (must never be broken):
  - Never compute prices, totals, or tax — ERPNext does that.
  - Every mutating call uses an idempotency_key derived from conversation_id.
  - M-Pesa STK push is the final step.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import structlog

from app.erp.customers import find_or_create_customer
from app.erp.payments import request_mpesa_payment
from app.erp.sales import create_sales_order, submit_sales_invoice
from app.erp.schemas import OrderItem

logger = structlog.get_logger(__name__)


@dataclass
class OrchestratorResult:
    status: str  # "payment_pending" | "error"
    invoice_name: str = ""
    sales_order_name: str = ""
    checkout_request_id: str = ""
    grand_total: Decimal = Decimal("0")
    payment_message: str = ""
    error: str = ""


class Orchestrator:
    """
    Executes the order-to-payment sequence against ERPNext.

    Called by AgentRouter when ConversationAgent returns action="order_confirmed".
    """

    def __init__(self, erp_client: Any, settings: Any) -> None:
        self._erp = erp_client
        self._settings = settings

    def _ikey(self, base: str, suffix: str) -> str:
        return hashlib.md5(f"{base}:{suffix}".encode()).hexdigest()[:16]

    async def process_order(
        self,
        conversation_id: int,
        phone: str,
        customer_name: str | None,
        items: list[dict],
        delivery_preference: str = "pickup",
        delivery_address: str = "",
    ) -> OrchestratorResult:
        """
        Full order flow: customer → SO → invoice → M-Pesa STK push.

        `items` comes directly from the confirm_order terminal tool arguments;
        each entry has "item_code" and "quantity" (or "qty").
        """
        base_key = str(conversation_id)

        # 1. Find or create customer
        try:
            customer = await find_or_create_customer(
                self._erp,
                phone=phone,
                name=customer_name,
                idempotency_key=self._ikey(base_key, "customer"),
            )
            logger.info("orchestrator_customer", customer=customer.name)
        except Exception as exc:
            logger.error("orchestrator_customer_failed", error=str(exc))
            return OrchestratorResult(status="error", error=f"Customer lookup failed: {exc}")

        # 2. Build OrderItem list
        order_items = [
            OrderItem(
                item_code=it["item_code"],
                qty=Decimal(str(it.get("quantity", it.get("qty", 1)))),
            )
            for it in items
        ]

        # 3. Create and submit Sales Order
        try:
            so_result = await create_sales_order(
                self._erp,
                customer=customer.name,
                items=order_items,
                idempotency_key=self._ikey(base_key, "so"),
                taxes_template=self._settings.erpnext_sales_taxes_template or None,
            )
            order = so_result["order"]
            grand_total: Decimal = so_result["total_breakdown"]["grand_total"]
            logger.info("orchestrator_so", name=order.name, total=str(grand_total))
        except Exception as exc:
            logger.error("orchestrator_so_failed", error=str(exc))
            return OrchestratorResult(status="error", error=f"Sales order failed: {exc}")

        # 4. Create and submit Sales Invoice
        try:
            inv_result = await submit_sales_invoice(
                self._erp,
                sales_order_name=order.name,
                idempotency_key=self._ikey(base_key, "inv"),
            )
            invoice_name: str = inv_result["invoice_no"]
            logger.info("orchestrator_invoice", name=invoice_name)
        except Exception as exc:
            logger.error("orchestrator_invoice_failed", error=str(exc))
            return OrchestratorResult(
                status="error",
                sales_order_name=order.name,
                error=f"Invoice failed: {exc}",
            )

        # 5. Request M-Pesa payment
        try:
            pay_result = await request_mpesa_payment(
                self._erp,
                invoice_name=invoice_name,
                phone=phone,
                amount=grand_total,
                idempotency_key=self._ikey(base_key, "mpesa"),
            )
            logger.info(
                "orchestrator_payment_requested",
                invoice=invoice_name,
                checkout_id=pay_result.get("checkout_request_id"),
            )
        except Exception as exc:
            logger.error("orchestrator_payment_failed", error=str(exc))
            return OrchestratorResult(
                status="error",
                sales_order_name=order.name,
                invoice_name=invoice_name,
                grand_total=grand_total,
                error=f"M-Pesa request failed: {exc}",
            )

        return OrchestratorResult(
            status="payment_pending",
            invoice_name=invoice_name,
            sales_order_name=order.name,
            checkout_request_id=pay_result.get("checkout_request_id", ""),
            grand_total=grand_total,
            payment_message=pay_result.get(
                "message", "Please enter your M-Pesa PIN to complete the payment."
            ),
        )
