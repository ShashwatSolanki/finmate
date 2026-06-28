import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.db.models import User
from app.invoice.parse_invoice import parse_invoice_text
from app.invoice.pdf_invoice import build_invoice_pdf, build_invoice_pdf_from_structured
from app.invoice.schemas import ParseInvoiceResult, ParsedLineItem, StructuredInvoice
from app.invoice.text_extract import extract_invoice_text

router = APIRouter()

_MAX_UPLOAD_BYTES = 12 * 1024 * 1024  # 12 MB


class LineItem(BaseModel):
    description: str = Field(..., min_length=1, max_length=500)
    amount: Decimal = Field(..., description="Positive amount")
    quantity: float | None = None
    unit_price: Decimal | None = None


class InvoicePdfBody(BaseModel):
    line_items: list[LineItem] = Field(..., min_length=1)
    currency: str = Field(default="USD", max_length=8)
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    vendor_name: str | None = None
    bill_to: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None


def _structured_from_body(body: InvoicePdfBody) -> StructuredInvoice:
    items = [
        ParsedLineItem(
            description=li.description,
            amount=li.amount,
            quantity=li.quantity,
            unit_price=li.unit_price,
        )
        for li in body.line_items
    ]
    total = sum((i.amount for i in items), start=Decimal("0"))
    return StructuredInvoice(
        invoice_number=body.invoice_number,
        invoice_date=body.invoice_date,
        due_date=body.due_date,
        vendor_name=body.vendor_name,
        bill_to=body.bill_to,
        currency=body.currency,
        line_items=items,
        subtotal=body.subtotal,
        tax=body.tax,
        total=total,
    )


@router.post("/parse", response_model=ParseInvoiceResult)
async def parse_invoice_upload(
    file: UploadFile = File(...),
    current: User = Depends(get_current_user),
) -> ParseInvoiceResult:
    """Upload a PDF or image invoice; returns structured fields + OCR/PDF text preview."""
    _ = current
    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large (max 12 MB).")
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file.")

    filename = file.filename or "upload"
    try:
        source_type, text, warnings = extract_invoice_text(
            data=data,
            content_type=file.content_type,
            filename=filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    if not text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not extract text from this file. Try a clearer scan or a text-based PDF.",
        )
    print("=== OCR TEXT ===")
    print(repr(text))
    print("=== END OCR TEXT ===")

    return parse_invoice_text(text, source_type=source_type, filename=filename, warnings=warnings)


@router.post("/pdf")
def invoice_pdf(
    body: InvoicePdfBody,
    current: User = Depends(get_current_user),
) -> Response:
    structured = _structured_from_body(body)
    ref = (body.invoice_number or str(uuid.uuid4())[:8]).upper()[:24]
    pdf_bytes = build_invoice_pdf_from_structured(
        structured,
        invoice_ref=ref,
        bill_to_fallback=body.bill_to or current.email,
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="invoice-{ref}.pdf"'},
    )


@router.post("/pdf/structured")
def invoice_pdf_structured(
    body: StructuredInvoice,
    current: User = Depends(get_current_user),
) -> Response:
    """Generate PDF from a full StructuredInvoice (e.g. after editing parsed upload)."""
    ref = (body.invoice_number or str(uuid.uuid4())[:8]).upper()[:24]
    pdf_bytes = build_invoice_pdf_from_structured(
        body,
        invoice_ref=ref,
        bill_to_fallback=body.bill_to or current.email,
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="invoice-{ref}.pdf"'},
    )
