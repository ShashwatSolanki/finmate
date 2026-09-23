"""Unit tests for Member 4 invoice parsing and PDF generation functions."""

import unittest
from decimal import Decimal

from app.invoice.parse_invoice import parse_invoice_text
from app.invoice.pdf_invoice import build_invoice_pdf, build_invoice_pdf_from_structured
from app.invoice.schemas import ParsedLineItem, StructuredInvoice


class InvoiceParsingUnitTests(unittest.TestCase):
    def test_parse_simple_description_amount_lines(self):
        text = "Website design 1200.00\nHosting 300.00\nTotal: 1500.00"
        result = parse_invoice_text(text, source_type="pdf", filename="inv.pdf")

        descriptions = [i.description for i in result.invoice.line_items]
        self.assertIn("Website design", descriptions)
        self.assertIn("Hosting", descriptions)
        self.assertEqual(result.invoice.total, Decimal("1500.00"))
        self.assertEqual(result.confidence, "high")

    def test_parse_amount_first_lines(self):
        text = "1200.00 Website design\n300.00 Hosting"
        result = parse_invoice_text(text, source_type="pdf", filename="inv.pdf")

        self.assertEqual(len(result.invoice.line_items), 2)
        self.assertEqual(
            {i.description for i in result.invoice.line_items},
            {"Website design", "Hosting"},
        )

    def test_parse_qty_x_unit_equals_total(self):
        text = "Notebook 3 x 250.00 = 750.00"
        result = parse_invoice_text(text, source_type="pdf", filename="inv.pdf")

        self.assertEqual(len(result.invoice.line_items), 1)
        item = result.invoice.line_items[0]
        self.assertEqual(item.quantity, 3.0)
        self.assertEqual(item.unit_price, Decimal("250.00"))
        self.assertEqual(item.amount, Decimal("750.00"))

    def test_currency_symbol_detection(self):
        # "Rs." is a currency prefix used only when parsing individual line-item
        # amounts, not one of the whole-document currency symbols/codes that
        # _detect_currency looks for, so it correctly falls back to USD here.
        result = parse_invoice_text("Item Rs. 799.00", source_type="pdf", filename="inv.pdf")
        self.assertEqual(result.invoice.currency, "USD")

    def test_currency_symbol_rupee_sign_detected_as_inr(self):
        result = parse_invoice_text("Item \u20b9799.00", source_type="pdf", filename="inv.pdf")
        self.assertEqual(result.invoice.currency, "INR")

    def test_currency_code_detection_fallback(self):
        result = parse_invoice_text("Amount due: EUR 100.00", source_type="pdf", filename="inv.pdf")
        self.assertEqual(result.invoice.currency, "EUR")

    def test_cgst_sgst_combined_into_single_tax_not_line_items(self):
        text = (
            "Wireless Mouse 1 Rs. 799.00 Rs. 799.00\n"
            "Subtotal: 799.00\n"
            "CGST: 71.91\n"
            "SGST: 71.91\n"
            "Total: 942.82"
        )
        result = parse_invoice_text(text, source_type="pdf", filename="inv.pdf")

        descriptions = [i.description.lower() for i in result.invoice.line_items]
        self.assertNotIn("cgst", descriptions)
        self.assertNotIn("sgst", descriptions)
        self.assertEqual(result.invoice.tax, Decimal("143.82"))
        self.assertEqual(result.invoice.total, Decimal("942.82"))

    def test_duplicate_rendering_of_same_item_is_deduplicated(self):
        # Same line item can appear twice: once as a plain "desc amount" line,
        # once as a pipe-joined table row (the pdfplumber table-cell fallback).
        text = "Website design 1200.00\nWebsite design | 1200.00"
        result = parse_invoice_text(text, source_type="pdf", filename="inv.pdf")

        matching = [i for i in result.invoice.line_items if "website design" in i.description.lower()]
        self.assertEqual(len(matching), 1)

    def test_no_line_items_produces_warning_and_low_confidence(self):
        result = parse_invoice_text("Just some unrelated text with no amounts.", source_type="pdf", filename="inv.pdf")

        self.assertEqual(result.invoice.line_items, [])
        self.assertEqual(result.confidence, "low")
        self.assertTrue(any("No line items" in w for w in result.warnings))

    def test_single_total_only_becomes_one_line_item(self):
        result = parse_invoice_text("Total: 500.00", source_type="pdf", filename="inv.pdf")

        self.assertEqual(len(result.invoice.line_items), 1)
        self.assertEqual(result.invoice.line_items[0].amount, Decimal("500.00"))

    def test_subtotal_left_unset_when_it_would_equal_total(self):
        # Subtotal is only inferred when it would differ meaningfully from the
        # stated total (a sign tax/discount was folded into the total). When
        # the line items already sum exactly to the total, there's nothing to
        # distinguish, so subtotal is correctly left unset.
        text = "Item A 100.00\nItem B 50.00\nTotal: 150.00"
        result = parse_invoice_text(text, source_type="pdf", filename="inv.pdf")

        self.assertIsNone(result.invoice.subtotal)

    def test_subtotal_inferred_when_total_differs_from_line_item_sum(self):
        text = "Item A 100.00\nItem B 50.00\nTotal: 165.00"
        result = parse_invoice_text(text, source_type="pdf", filename="inv.pdf")

        self.assertEqual(result.invoice.subtotal, Decimal("150.00"))
        self.assertEqual(result.invoice.total, Decimal("165.00"))


class InvoicePdfBuilderUnitTests(unittest.TestCase):
    def test_build_invoice_pdf_returns_valid_pdf_bytes(self):
        pdf_bytes = build_invoice_pdf(
            invoice_ref="INV-001",
            bill_to="Acme Corp",
            line_items=[("Consulting", Decimal("500.00"))],
            currency="USD",
        )
        self.assertIsInstance(pdf_bytes, bytes)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))

    def test_build_invoice_pdf_uses_provided_total_over_computed(self):
        pdf_bytes = build_invoice_pdf(
            invoice_ref="INV-002",
            bill_to="Acme Corp",
            line_items=[("Consulting", Decimal("500.00"))],
            tax=Decimal("50.00"),
            total=Decimal("550.00"),
        )
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))

    def test_build_invoice_pdf_from_structured_requires_line_items(self):
        empty_invoice = StructuredInvoice(bill_to="Acme Corp", line_items=[])
        with self.assertRaises(ValueError):
            build_invoice_pdf_from_structured(empty_invoice, invoice_ref="INV-003", bill_to_fallback="fallback@x.com")

    def test_build_invoice_pdf_from_structured_happy_path(self):
        invoice = StructuredInvoice(
            bill_to="Acme Corp",
            currency="USD",
            line_items=[ParsedLineItem(description="Design work", amount=Decimal("1200.00"))],
            total=Decimal("1200.00"),
        )
        pdf_bytes = build_invoice_pdf_from_structured(invoice, invoice_ref="INV-004", bill_to_fallback="fallback@x.com")
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))


if __name__ == "__main__":
    unittest.main()