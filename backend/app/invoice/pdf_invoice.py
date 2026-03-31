"""PDF invoice generation (reportlab)."""

from io import BytesIO
from decimal import Decimal

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def build_invoice_pdf(
    *,
    invoice_ref: str,
    bill_to: str,
    line_items: list[tuple[str, Decimal]],
    currency: str = "USD",
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
    c.drawString(72, y, f"Bill to: {bill_to}")
    y -= 24
    c.setFont("Helvetica-Bold", 11)
    c.drawString(72, y, "Description")
    c.drawString(420, y, f"Amount ({currency})")
    y -= 16
    c.setFont("Helvetica", 10)
    total = Decimal("0")
    for desc, amt in line_items:
        total += amt
        c.drawString(72, y, desc[:80])
        c.drawRightString(540, y, f"{amt:.2f}")
        y -= 14
        if y < 100:
            c.showPage()
            y = height - 72
            c.setFont("Helvetica", 10)
    y -= 10
    c.setFont("Helvetica-Bold", 11)
    c.drawString(72, y, "Total")
    c.drawRightString(540, y, f"{total:.2f}")
    c.save()
    return buf.getvalue()
