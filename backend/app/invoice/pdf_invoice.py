"""PDF invoice generation (reportlab)."""

from __future__ import annotations

from io import BytesIO
from decimal import Decimal
import textwrap

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
    tax_breakdown: list[tuple[str, Decimal]] | None = None,
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
        desc_lines = textwrap.wrap(desc, width=42 if has_item_details else 80) or [desc]
        quantity, unit_price = item_details[index] if (
            has_item_details and item_details is not None and index < len(item_details)
        ) else (None, None)

        for line_index, desc_line in enumerate(desc_lines):
            c.drawString(72, y, desc_line)
            if line_index == 0 and has_item_details:
                if quantity is not None:
                    quantity_text = f"{quantity:g}" if isinstance(quantity, (int, float)) else str(quantity)
                    c.drawRightString(348, y, quantity_text)
                if unit_price is not None:
                    c.drawRightString(430, y, f"{unit_price:.2f}")
                c.drawRightString(540, y, f"{amt:.2f}")
            elif line_index == 0:
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
    if tax_breakdown:
        for label, amount in tax_breakdown:
            c.drawString(72, y, label[:80])
            c.drawRightString(540, y, f"{amount:.2f}")
            y -= 14
        aggregate_tax = tax if tax is not None else sum(
            (amount for _label, amount in tax_breakdown), start=Decimal("0")
        )
        c.setFont("Helvetica-Bold", 9)
        c.drawString(72, y, "Total Tax")
        c.drawRightString(540, y, f"{aggregate_tax:.2f}")
        c.setFont("Helvetica", 9)
        y -= 14
    elif tax is not None:
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


def _validate_invoice_for_pdf(invoice: StructuredInvoice) -> None:
    """Reject inconsistent structured amounts before rendering a customer-facing artifact."""
    if not invoice.line_items:
        raise ValueError("At least one line item is required to generate a PDF.")

    item_sum = sum((item.amount for item in invoice.line_items), start=Decimal("0"))
    tolerance = Decimal("0.02")

    for index, item in enumerate(invoice.line_items, start=1):
        if item.quantity is not None and item.quantity <= 0:
            raise ValueError(f"Line item {index} quantity must be greater than zero.")
        if item.unit_price is not None and item.unit_price < 0:
            raise ValueError(f"Line item {index} unit price cannot be negative.")
        if item.quantity is not None and item.unit_price is not None:
            expected_amount = (
                Decimal(str(item.quantity)) * item.unit_price
            ).quantize(Decimal("0.01"))
            if abs(expected_amount - item.amount) > tolerance:
                raise ValueError(
                    f"Line item {index} amount ({item.amount:.2f}) does not match "
                    f"quantity × unit price ({expected_amount:.2f}). Correct the item before exporting."
                )

    if invoice.subtotal is not None and abs(item_sum - invoice.subtotal) > tolerance:
        raise ValueError(
            f"Invoice subtotal ({invoice.subtotal:.2f}) does not match the sum of line items "
            f"({item_sum:.2f}). Correct the invoice before exporting."
        )

    breakdown_tax = sum(
        (component.amount for component in invoice.tax_breakdown), start=Decimal("0")
    )
    if invoice.tax_breakdown and invoice.tax is not None and abs(breakdown_tax - invoice.tax) > tolerance:
        raise ValueError(
            f"Tax breakdown ({breakdown_tax:.2f}) does not match aggregate tax ({invoice.tax:.2f})."
        )

    if invoice.total is not None:
        base_amount = invoice.subtotal if invoice.subtotal is not None else item_sum
        effective_tax = invoice.tax if invoice.tax is not None else breakdown_tax
        expected_total = base_amount + effective_tax
        if abs(expected_total - invoice.total) > tolerance:
            raise ValueError(
                f"Invoice total ({invoice.total:.2f}) does not match subtotal/line items plus tax "
                f"({expected_total:.2f}). Correct the invoice before exporting."
            )


def build_invoice_pdf_from_structured(
    invoice: StructuredInvoice,
    *,
    invoice_ref: str,
    bill_to_fallback: str,
) -> bytes:
    _validate_invoice_for_pdf(invoice)
    items = [(li.description, li.amount) for li in invoice.line_items]
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
        tax_breakdown=[(component.label, component.amount) for component in invoice.tax_breakdown],
    )
