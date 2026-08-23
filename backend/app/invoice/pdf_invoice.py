"""PDF invoice generation (reportlab)."""

from __future__ import annotations

from io import BytesIO
from decimal import Decimal

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.invoice.schemas import StructuredInvoice


def build_invoice_pdf(
    *,
    invoice_ref: str,
    bill_to: str,
    line_items: list[tuple[str, Decimal]],
    currency: str = "USD",
    vendor_name: str | None = None,
    invoice_date: str | None = None,
    due_date: str | None = None,
    subtotal: Decimal | None = None,
    tax: Decimal | None = None,
    total: Decimal | None = None,
) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    _, height = letter
    y = height - 72

    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, y, "Invoice")
    y -= 28

    c.setFont("Helvetica", 10)
    c.drawString(72, y, f"Invoice #: {invoice_ref}")
    y -= 14
    if vendor_name:
        c.drawString(72, y, f"From: {vendor_name[:80]}")
        y -= 14
    c.drawString(72, y, f"Bill to: {bill_to[:100]}")
    y -= 14
    if invoice_date:
        c.drawString(72, y, f"Date: {invoice_date[:40]}")
        y -= 14
    if due_date:
        c.drawString(72, y, f"Due: {due_date[:40]}")
        y -= 14

    y -= 10
    c.setFont("Helvetica-Bold", 11)
    c.drawString(72, y, "Description")
    c.drawString(420, y, f"Amount ({currency})")
    y -= 16
    c.setFont("Helvetica", 10)

    line_total = Decimal("0")
    for desc, amt in line_items:
        line_total += amt
        c.drawString(72, y, desc[:80])
        c.drawRightString(540, y, f"{amt:.2f}")
        y -= 14
        if y < 120:
            c.showPage()
            y = height - 72
            c.setFont("Helvetica", 10)

    if subtotal is not None:
        y -= 6
        c.drawString(72, y, "Subtotal")
        c.drawRightString(540, y, f"{subtotal:.2f}")
        y -= 14
    if tax is not None:
        c.drawString(72, y, "Tax")
        c.drawRightString(540, y, f"{tax:.2f}")
        y -= 14

    y -= 6
    c.setFont("Helvetica-Bold", 11)
    c.drawString(72, y, "Total")
    final_total = total if total is not None else line_total + (tax or Decimal("0"))
    c.drawRightString(540, y, f"{final_total:.2f}")
    c.save()
    return buf.getvalue()


def build_invoice_pdf_from_structured(
    invoice: StructuredInvoice,
    *,
    invoice_ref: str,
    bill_to_fallback: str,
) -> bytes:
    items = [(li.description, li.amount) for li in invoice.line_items]
    if not items:
        raise ValueError("At least one line item is required to generate a PDF.")
    return build_invoice_pdf(
        invoice_ref=invoice_ref,
        bill_to=invoice.bill_to or bill_to_fallback,
        line_items=items,
        currency=invoice.currency,
        vendor_name=invoice.vendor_name,
        invoice_date=invoice.invoice_date,
        due_date=invoice.due_date,
        subtotal=invoice.subtotal,
        tax=invoice.tax,
        total=invoice.total,
    )
