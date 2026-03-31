import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.db.models import User
from app.invoice.pdf_invoice import build_invoice_pdf

router = APIRouter()


class LineItem(BaseModel):
    description: str = Field(..., min_length=1, max_length=500)
    amount: Decimal = Field(..., description="Positive amount")


class InvoicePdfBody(BaseModel):
    line_items: list[LineItem] = Field(..., min_length=1)
    currency: str = Field(default="USD", max_length=8)


@router.post("/pdf")
def invoice_pdf(
    body: InvoicePdfBody,
    current: User = Depends(get_current_user),
) -> Response:
    ref = str(uuid.uuid4())[:8].upper()
    items = [(li.description, li.amount) for li in body.line_items]
    bill_to = current.email
    pdf_bytes = build_invoice_pdf(
        invoice_ref=ref,
        bill_to=bill_to,
        line_items=items,
        currency=body.currency,
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="invoice-{ref}.pdf"'},
    )
