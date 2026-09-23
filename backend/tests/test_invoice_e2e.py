"""End-to-end tests for Member 4 invoice API routes.

Hits the real FastAPI routes through TestClient (routing, request
validation, response shape, headers) with the auth dependency overridden
so these tests don't require a live database or login flow.

NOTE: adjust the `from app.main import app` import below if your FastAPI
app instance lives at a different module path.
"""

import io
import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.main import app

_FAKE_USER = SimpleNamespace(email="test-user@example.com")


def _override_get_current_user():
    return _FAKE_USER


class InvoicePdfEndpointE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.dependency_overrides[get_current_user] = _override_get_current_user
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.pop(get_current_user, None)

    def test_pdf_endpoint_returns_pdf_for_valid_line_items(self):
        body = {
            "line_items": [{"description": "Consulting", "amount": "500.00"}],
            "currency": "USD",
            "bill_to": "Acme Corp",
        }
        response = self.client.post("/api/invoices/pdf", json=body)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF-"))

    def test_pdf_endpoint_rejects_empty_line_items(self):
        body = {"line_items": [], "currency": "USD"}
        response = self.client.post("/api/invoices/pdf", json=body)

        self.assertEqual(response.status_code, 422)

    def test_pdf_structured_endpoint_returns_pdf(self):
        body = {
            "bill_to": "Acme Corp",
            "currency": "USD",
            "line_items": [{"description": "Design work", "amount": "1200.00"}],
            "total": "1200.00",
        }
        response = self.client.post("/api/invoices/pdf/structured", json=body)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF-"))

    def test_pdf_structured_endpoint_raises_on_empty_line_items(self):
        # Unlike /pdf (which validates line_items via LineItem's min_length=1),
        # /pdf/structured accepts a raw StructuredInvoice with no such
        # constraint, so an empty list passes request validation and then
        # build_invoice_pdf_from_structured raises a plain ValueError that
        # the route does not catch -- it surfaces as an unhandled exception
        # (a 500 in production) instead of a clean 4xx. This test documents
        # that current behavior; consider catching ValueError in the route
        # and returning HTTPException(422, ...) to match /pdf's behavior.
        body = {"bill_to": "Acme Corp", "currency": "USD", "line_items": []}
        with self.assertRaises(ValueError):
            self.client.post("/api/invoices/pdf/structured", json=body)


class InvoiceCsvParseEndpointE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.dependency_overrides[get_current_user] = _override_get_current_user
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.pop(get_current_user, None)

    def test_parse_csv_endpoint_returns_structured_invoice(self):
        csv_content = (
            "invoice_no,item,quantity,unit_price,amount,subtotal,cgst,sgst,total\n"
            "INV-500,Wireless Mouse,1,799.00,799.00,799.00,71.91,71.91,942.82\n"
        )
        files = {"file": ("invoice.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}

        response = self.client.post("/api/invoices/parse/csv", files=files)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["invoice"]["invoice_number"], "INV-500")
        self.assertEqual(payload["invoice"]["line_items"][0]["description"], "Wireless Mouse")
        self.assertEqual(payload["source_type"], "csv")

    def test_parse_csv_endpoint_rejects_invalid_csv_format(self):
        csv_content = "not_item,not_amount\nfoo,bar\n"
        files = {"file": ("bad.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}

        response = self.client.post("/api/invoices/parse/csv", files=files)

        self.assertEqual(response.status_code, 422)

    def test_parse_csv_endpoint_rejects_empty_file(self):
        files = {"file": ("empty.csv", io.BytesIO(b""), "text/csv")}

        response = self.client.post("/api/invoices/parse/csv", files=files)

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()