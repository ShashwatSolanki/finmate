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
    item_details: list[tuple[float | None, Decimal | None]] | None = None,
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

    has_item_details = item_details is not None and any(
        quantity is not None or unit_price is not None
        for quantity, unit_price in item_details
    )

    def draw_table_header() -> None:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(72, y, "Description")
        if has_item_details:
            c.drawRightString(348, y, "Qty")
            c.drawRightString(430, y, f"Unit Price ({currency})")
            c.drawRightString(540, y, f"Amount ({currency})")
        else:
            c.drawRightString(540, y, f"Amount ({currency})")
        c.setFont("Helvetica", 9)

    y -= 10
    draw_table_header()
    y -= 16

    line_total = Decimal("0")
    for index, (desc, amt) in enumerate(line_items):
        line_total += amt
        c.drawString(72, y, desc[:42] if has_item_details else desc[:80])
        if has_item_details:
            quantity, unit_price = item_details[index] if index < len(item_details) else (None, None)
            if quantity is not None:
                quantity_text = f"{quantity:g}" if isinstance(quantity, (int, float)) else str(quantity)
                c.drawRightString(348, y, quantity_text)
            if unit_price is not None:
                c.drawRightString(430, y, f"{unit_price:.2f}")
        c.drawRightString(540, y, f"{amt:.2f}")
        y -= 14
        if y < 120:
            c.showPage()
            y = height - 72
            c.setFont("Helvetica", 10)
            draw_table_header()
            y -= 16

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
    item_details = [(li.quantity, li.unit_price) for li in invoice.line_items]
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
        item_details=item_details,
    )
