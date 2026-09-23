"""Integration tests for Member 4 invoice extraction + parsing pipeline.

These build a real text-based PDF with reportlab (no Tesseract/OCR binary
required) and verify that text_extract.py and parse_invoice.py work
correctly together end-to-end, matching how an uploaded PDF is processed
by the /parse endpoint.
"""

import unittest
from decimal import Decimal
from io import BytesIO

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.invoice.parse_invoice import parse_invoice_text
from app.invoice.text_extract import extract_invoice_text, extract_text_from_pdf


def _build_sample_invoice_pdf() -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    _, height = letter
    y = height - 72
    lines = [
        "Invoice #: INV-2026-001",
        "Bill to: Acme Corp",
        "Website design 1200.00",
        "Hosting 300.00",
        "Subtotal: 1500.00",
        "Tax: 150.00",
        "Total: 1650.00",
    ]
    c.setFont("Helvetica", 11)
    for line in lines:
        c.drawString(72, y, line)
        y -= 16
    c.save()
    return buf.getvalue()


class InvoicePdfExtractionIntegrationTests(unittest.TestCase):
    def test_pdf_text_extraction_feeds_parser_correctly(self):
        pdf_bytes = _build_sample_invoice_pdf()

        text, warnings = extract_text_from_pdf(pdf_bytes)
        self.assertIn("Website design", text)

        result = parse_invoice_text(text, source_type="pdf", filename="sample.pdf")

        self.assertEqual(result.invoice.invoice_number, "INV-2026-001")
        self.assertEqual(result.invoice.total, Decimal("1650.00"))
        self.assertEqual(result.invoice.tax, Decimal("150.00"))
        descriptions = {i.description for i in result.invoice.line_items}
        self.assertIn("Website design", descriptions)
        self.assertIn("Hosting", descriptions)
        self.assertEqual(result.confidence, "high")

    def test_extract_invoice_text_routes_pdf_by_content_type(self):
        pdf_bytes = _build_sample_invoice_pdf()

        source_type, text, warnings = extract_invoice_text(
            data=pdf_bytes, content_type="application/pdf", filename="sample.pdf"
        )
        self.assertEqual(source_type, "pdf")
        self.assertIn("Total", text)

    def test_extract_invoice_text_routes_pdf_by_filename_when_no_content_type(self):
        pdf_bytes = _build_sample_invoice_pdf()

        source_type, text, warnings = extract_invoice_text(
            data=pdf_bytes, content_type=None, filename="sample.pdf"
        )
        self.assertEqual(source_type, "pdf")

    def test_extract_invoice_text_rejects_unsupported_file_type(self):
        with self.assertRaises(ValueError):
            extract_invoice_text(data=b"not a real file", content_type="text/plain", filename="notes.txt")

    def test_extract_text_from_pdf_rejects_non_pdf_bytes(self):
        with self.assertRaises(ValueError):
            extract_text_from_pdf(b"this is definitely not a pdf")

    def test_empty_pdf_page_produces_low_confidence_result_not_crash(self):
        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.save()  # blank page, no text
        blank_pdf = buf.getvalue()

        text, warnings = extract_text_from_pdf(blank_pdf)
        result = parse_invoice_text(text, source_type="pdf", filename="blank.pdf")

        self.assertEqual(result.invoice.line_items, [])
        self.assertEqual(result.confidence, "low")


if __name__ == "__main__":
    unittest.main()
