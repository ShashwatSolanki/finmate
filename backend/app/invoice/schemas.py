"""Structured invoice models shared by parser, API, and PDF builder."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class ParsedLineItem(BaseModel):
    description: str = Field(..., min_length=1, max_length=500)
    quantity: float | None = None
    unit_price: Decimal | None = None
    amount: Decimal = Field(..., description="Line total")


class StructuredInvoice(BaseModel):
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    vendor_name: str | None = None
    bill_to: str | None = None
    currency: str = "USD"
    line_items: list[ParsedLineItem] = Field(default_factory=list)
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    notes: str | None = None


class ParseInvoiceResult(BaseModel):
    source_type: str  # pdf | image
    filename: str
    confidence: str  # high | medium | low
    extracted_text_preview: str
    invoice: StructuredInvoice
    warnings: list[str] = Field(default_factory=list)
