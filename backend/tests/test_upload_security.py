import io
import unittest

from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.api.routes.invoices import _MAX_UPLOAD_BYTES, _read_upload_limited
from app.invoice.text_extract import extract_invoice_text


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
