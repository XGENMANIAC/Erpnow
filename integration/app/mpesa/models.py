"""Pydantic models for Safaricom Daraja API payloads and STK callbacks."""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class STKPushResult(BaseModel):
    """Successful STK push initiation response."""

    checkout_request_id: str
    merchant_request_id: str
    customer_message: str = ""


class STKCallback(BaseModel):
    """Parsed M-Pesa STK callback from Daraja."""

    merchant_request_id: str
    checkout_request_id: str
    result_code: int
    result_desc: str
    mpesa_ref: str | None = None
    amount: Decimal | None = None
    phone: str | None = None
    transaction_date: str | None = None

    @property
    def is_success(self) -> bool:
        return self.result_code == 0


def parse_stk_callback(body: dict) -> STKCallback:
    """
    Parse the nested Daraja STK callback payload into a flat model.

    Daraja shape:
      {"Body": {"stkCallback": {"MerchantRequestID": ..., "CallbackMetadata": {"Item": [...]}}}}
    """
    stk = body["Body"]["stkCallback"]
    metadata: dict = {}

    if stk.get("ResultCode") == 0:
        for item in stk.get("CallbackMetadata", {}).get("Item", []):
            if item.get("Value") is not None:
                metadata[item["Name"]] = item["Value"]

    return STKCallback(
        merchant_request_id=stk["MerchantRequestID"],
        checkout_request_id=stk["CheckoutRequestID"],
        result_code=stk["ResultCode"],
        result_desc=stk["ResultDesc"],
        mpesa_ref=metadata.get("MpesaReceiptNumber"),
        amount=Decimal(str(metadata["Amount"])) if "Amount" in metadata else None,
        phone=str(metadata["PhoneNumber"]) if "PhoneNumber" in metadata else None,
        transaction_date=str(metadata["TransactionDate"]) if "TransactionDate" in metadata else None,
    )
