"""
Pydantic v2 models mirroring the ERPNext documents used by this service.

Rules:
- Use Decimal for all monetary amounts (never float).
- Fields mirror ERPNext field names exactly so dicts can be passed through.
- Optional fields default to None / sensible defaults for creation payloads.
"""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class ERPCustomer(BaseModel):
    name: str  # ERPNext document name / ID (auto-assigned on creation)
    customer_name: str
    mobile_no: str | None = None
    custom_phone: str | None = None
    customer_type: str = "Individual"
    customer_group: str = "All Customer Groups"
    territory: str = "Kenya"


class ERPItem(BaseModel):
    name: str
    item_name: str
    item_code: str
    description: str | None = None
    stock_uom: str = "Nos"
    is_stock_item: bool = True


class ERPItemPrice(BaseModel):
    item_code: str
    price_list: str
    currency: str = "KES"
    price_list_rate: Decimal


class ERPStockBin(BaseModel):
    item_code: str
    warehouse: str
    actual_qty: Decimal = Decimal("0")
    reserved_qty: Decimal = Decimal("0")
    projected_qty: Decimal = Decimal("0")


class OrderItem(BaseModel):
    item_code: str
    qty: Decimal
    rate: Decimal | None = None  # None → ERPNext uses price list rate


class ERPSalesOrder(BaseModel):
    name: str
    customer: str
    items: list[dict] = Field(default_factory=list)
    grand_total: Decimal = Decimal("0")
    total_taxes_and_charges: Decimal = Decimal("0")
    status: str = "Draft"
    docstatus: int = 0  # 0=draft, 1=submitted, 2=cancelled


class ERPSalesInvoice(BaseModel):
    name: str
    customer: str
    sales_order: str | None = None
    grand_total: Decimal = Decimal("0")
    outstanding_amount: Decimal = Decimal("0")
    status: str = "Draft"
    docstatus: int = 0


class ERPPaymentEntry(BaseModel):
    name: str
    party: str
    paid_amount: Decimal
    reference_no: str  # M-Pesa transaction code
    docstatus: int = 0


class ERPQuotation(BaseModel):
    name: str
    customer: str | None = None
    party_name: str | None = None
    items: list[dict] = Field(default_factory=list)
    grand_total: Decimal = Decimal("0")
    docstatus: int = 0
