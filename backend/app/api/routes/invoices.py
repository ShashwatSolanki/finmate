import uuid
import csv
import io
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


def _csv_decimal(value: str | None) -> Decimal | None:
    raw = (value or "").strip().replace(",", "").replace("₹", "")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except Exception:
        return None


def _csv_invoice(data: bytes, filename: str) -> ParseInvoiceResult:
    """Convert the common one-row-per-item invoice CSV format into one invoice."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    headers = {str(h or "").strip().lower() for h in (reader.fieldnames or [])}
    if not {"item", "amount"}.issubset(headers):
        raise ValueError("This CSV is not an invoice format. Expected at least item and amount columns.")
    rows = list(reader)
    if not rows:
        raise ValueError("The invoice CSV has no data rows.")
    first = {str(k or "").strip().lower(): v for k, v in rows[0].items()}
    items: list[ParsedLineItem] = []
    for index, row in enumerate(rows, start=2):
        record = {str(k or "").strip().lower(): v for k, v in row.items()}
        description = (record.get("item") or "").strip()
        amount = _csv_decimal(record.get("amount"))
        if not description or amount is None:
            raise ValueError(f"Invoice CSV row {index} needs both item and amount.")
        quantity_raw = _csv_decimal(record.get("quantity"))
        unit_price = _csv_decimal(record.get("unit_price"))
        items.append(ParsedLineItem(description=description, amount=amount, quantity=float(quantity_raw) if quantity_raw is not None else None, unit_price=unit_price))
    cgst = _csv_decimal(first.get("cgst")) or Decimal("0")
    sgst = _csv_decimal(first.get("sgst")) or Decimal("0")
    tax = cgst + sgst
    subtotal = _csv_decimal(first.get("subtotal")) or sum((item.amount for item in items), start=Decimal("0"))
    total = _csv_decimal(first.get("total")) or subtotal + tax
    invoice = StructuredInvoice(
        invoice_number=(first.get("invoice_no") or first.get("invoice_number") or None),
        invoice_date=first.get("invoice_date") or None,
        vendor_name=first.get("seller") or first.get("vendor") or None,
        bill_to=first.get("buyer") or first.get("bill_to") or None,
        currency=(first.get("currency") or "USD").upper(),
        line_items=items,
        subtotal=subtotal,
        tax=tax or None,
        total=total,
        notes="Imported from invoice CSV",
    )
    return ParseInvoiceResult(source_type="csv", filename=filename, confidence="high", extracted_text_preview=text[:2000], invoice=invoice)


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
    subtotal = body.subtotal if body.subtotal is not None else sum((i.amount for i in items), start=Decimal("0"))
    total = subtotal + (body.tax or Decimal("0"))
    return StructuredInvoice(
        invoice_number=body.invoice_number,
        invoice_date=body.invoice_date,
        due_date=body.due_date,
        vendor_name=body.vendor_name,
        bill_to=body.bill_to,
        currency=body.currency,
        line_items=items,
        subtotal=subtotal,
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
    return parse_invoice_text(text, source_type=source_type, filename=filename, warnings=warnings)


@router.post("/parse/csv", response_model=ParseInvoiceResult)
async def parse_invoice_csv_upload(
    file: UploadFile = File(...),
    current: User = Depends(get_current_user),
) -> ParseInvoiceResult:
    _ = current
    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large (max 12 MB).")
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file.")
    try:
        return _csv_invoice(data, file.filename or "invoice.csv")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


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
