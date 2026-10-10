import io
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.api.routes.invoices import _MAX_UPLOAD_BYTES, _read_upload_limited
from app.invoice.text_extract import (
    _MAX_PDF_PAGES,
    _MAX_PDF_RENDER_PIXELS,
    _OCR_TIMEOUT_SECONDS,
    _pdf_ocr_fallback,
    _ocr_image,
    extract_invoice_text,
    extract_text_from_pdf,
)


class UploadSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_within_limit_is_read_completely(self):
        data = b"safe input"
        upload = UploadFile(filename="small.txt", file=io.BytesIO(data))

        self.assertEqual(await _read_upload_limited(upload, limit=32), data)

    async def test_oversized_upload_is_rejected_before_full_read(self):
        upload = UploadFile(
            filename="large.bin",
            file=io.BytesIO(b"x" * (_MAX_UPLOAD_BYTES + 1)),
        )
        with self.assertRaises(HTTPException) as raised:
            await _read_upload_limited(upload)

        self.assertEqual(raised.exception.status_code, 413)

    def test_filename_and_content_type_cannot_make_invalid_bytes_an_image(self):
        with self.assertRaises(ValueError):
            extract_invoice_text(
                data=b"not an image",
                content_type="image/png",
                filename="invoice.png",
            )

    def test_filename_and_content_type_cannot_make_invalid_bytes_a_pdf(self):
        with self.assertRaises(ValueError):
            extract_invoice_text(
                data=b"not a pdf",
                content_type="application/pdf",
                filename="invoice.pdf",
            )


    def test_pdf_page_limit_is_checked_before_text_extraction(self):
        class FakeDoc:
            page_count = _MAX_PDF_PAGES + 1

            def close(self):
                self.closed = True

        fake_fitz = SimpleNamespace(open=lambda **kwargs: FakeDoc())
        with patch.dict(sys.modules, {"fitz": fake_fitz}):
            with self.assertRaisesRegex(ValueError, f"{_MAX_PDF_PAGES} pages"):
                extract_text_from_pdf(b"%PDF-1.7 test")

    def test_pdf_ocr_rejects_huge_page_before_rendering(self):
        class FakePage:
            rect = SimpleNamespace(width=50_000, height=50_000)

            def get_pixmap(self, **kwargs):
                raise AssertionError("Oversized page must be rejected before rendering")

        class FakeDoc:
            page_count = 1

            def load_page(self, index):
                return FakePage()

            def close(self):
                self.closed = True

        fake_fitz = SimpleNamespace(open=lambda **kwargs: FakeDoc())
        with patch.dict(sys.modules, {"fitz": fake_fitz}):
            with self.assertRaisesRegex(ValueError, "rendering limit"):
                _pdf_ocr_fallback(b"%PDF-1.7 test")

    def test_ocr_timeout_is_bounded_and_reported_as_input_error(self):
        def timeout(*args, **kwargs):
            self.assertEqual(kwargs["timeout"], _OCR_TIMEOUT_SECONDS)
            raise RuntimeError("Tesseract process timeout")

        fake_tesseract = SimpleNamespace(
            TesseractNotFoundError=type("TesseractNotFoundError", (Exception,), {}),
            image_to_string=timeout,
        )
        with patch.dict(sys.modules, {"pytesseract": fake_tesseract}):
            with patch("app.invoice.text_extract._configure_tesseract"):
                with self.assertRaisesRegex(ValueError, f"{_OCR_TIMEOUT_SECONDS}-second"):
                    _ocr_image(Image.new("RGB", (2, 2)))
