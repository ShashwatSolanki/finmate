"""Invoice Generator — parse text/PDF-style content, structured output, PDF handoff."""

from __future__ import annotations

import json
import re
import uuid
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy.orm import Session

from app.agents.types import AgentName, AgentResult
from app.config import settings
from app.invoice.parse_invoice import parse_invoice_text
from app.invoice.schemas import ParsedLineItem, StructuredInvoice
from app.ml.finmate import generate

_AMOUNT_LINE = re.compile(r"^\s*(?:[-*]\s*)?([\d.,]+)\s+(.+?)\s*$|^\s*(?:[-*]\s*)?(.+?)\s+(?:₹|Rs\.?|INR|\$)?\s*([\d,]+(?:\.\d{1,2})?)\s*$", re.M | re.I)


def _parse_simple_lines(message: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    total = Decimal("0")
    for m in _AMOUNT_LINE.finditer(message):
        amount_first = m.group(1) is not None
        amt = m.group(1) if amount_first else m.group(4)
        desc = (m.group(2) if amount_first else m.group(3)).strip()
        try:
            val = Decimal(amt.replace(",", ""))
        except InvalidOperation:
            continue
        if val <= 0:
            continue
        items.append({"description": desc, "amount": f"{val:.2f}"})
        total += val
    return items


def _structured_from_message(message: str) -> StructuredInvoice | None:
    """Try full invoice parse on pasted OCR/PDF text; fall back to simple line format."""
    simple = _parse_simple_lines(message)
    if simple and len(message.strip()) < 80:
        line_items = [ParsedLineItem(description=x["description"], amount=Decimal(x["amount"])) for x in simple]
        total = sum((i.amount for i in line_items), start=Decimal("0"))
        return StructuredInvoice(line_items=line_items, total=total, currency="USD")

    result = parse_invoice_text(message, source_type="text", filename="chat-message.txt")
    if result.invoice.line_items:
        return result.invoice

    if simple:
        line_items = [ParsedLineItem(description=x["description"], amount=Decimal(x["amount"])) for x in simple]
        total = sum((i.amount for i in line_items), start=Decimal("0"))
        return StructuredInvoice(line_items=line_items, total=total, currency=result.invoice.currency)
    return None


def _format_reply(invoice: StructuredInvoice, inv_id: str, *, source_note: str) -> str:
    lines = [
        f"Invoice ref: #{inv_id}",
    ]
    if invoice.vendor_name:
        lines.append(f"Vendor: {invoice.vendor_name}")
    if invoice.bill_to:
        lines.append(f"Bill to: {invoice.bill_to}")
    if invoice.invoice_date:
        lines.append(f"Date: {invoice.invoice_date}")
    lines.append("")
    lines.append("Line items:")
    for li in invoice.line_items[:12]:
        qty = f" ({li.quantity} x {li.unit_price})" if li.quantity and li.unit_price else ""
        lines.append(f"  - {li.description}{qty}: {li.amount:.2f} {invoice.currency}")
    if invoice.subtotal is not None:
        lines.append(f"Subtotal: {invoice.subtotal:.2f} {invoice.currency}")
    if invoice.tax is not None:
        lines.append(f"Tax: {invoice.tax:.2f} {invoice.currency}")
    if invoice.total is not None:
        lines.append(f"Total: {invoice.total:.2f} {invoice.currency}")

    payload = {
        "invoice_number": invoice.invoice_number or inv_id,
        "invoice_date": invoice.invoice_date,
        "due_date": invoice.due_date,
        "vendor_name": invoice.vendor_name,
        "bill_to": invoice.bill_to,
        "currency": invoice.currency,
        "line_items": [
            {
                "description": li.description,
                "amount": str(li.amount),
                **({"quantity": li.quantity} if li.quantity else {}),
                **({"unit_price": str(li.unit_price)} if li.unit_price else {}),
            }
            for li in invoice.line_items
        ],
        "subtotal": str(invoice.subtotal) if invoice.subtotal is not None else None,
        "tax": str(invoice.tax) if invoice.tax is not None else None,
    }

    prose = (
        "Structured invoice data extracted. Review the fields below, then generate a PDF from Settings "
        "(Invoice import) or POST /api/invoices/pdf/structured with this payload.\n\n"
        + "\n".join(lines)
    )

    json_tail = json.dumps(
        {
            "intent": "create_invoice",
            "steps": ["Review parsed fields", "Edit in Settings if needed", "Download PDF"],
            "tools_needed": ["parse_invoice_upload", "render_invoice_pdf"],
            "notes": source_note,
            "structured": payload,
        },
        ensure_ascii=False,
    )

    return f"[AGENT: INVOICE]\n\n{prose}\n\n{json_tail}"


def run(
    user_id: UUID,
    message: str,
    db: Session,
    rag_context: str | None = None,
) -> AgentResult:
    _ = db
    inv_id = str(uuid.uuid4())[:8].upper()
    invoice = _structured_from_message(message)

    rag_block = ""
    if rag_context and rag_context.strip():
        rag_block = "\n\n[Past context]\n" + rag_context.strip()[:2000]

    if invoice and invoice.line_items:
        if settings.finmate_use_llm:
            enriched = (
                f"{message}\n\n[Parsed invoice]\n{invoice.model_dump_json()}{rag_block}"
            )
            try:
                reply = generate(enriched)
                source = "llm"
            except Exception:
                reply = _format_reply(invoice, inv_id, source_note="parsed from message text")
                source = "structured_parse"
        else:
            reply = _format_reply(invoice, inv_id, source_note="parsed from message text")
            source = "structured_parse"

        total = invoice.total or sum((i.amount for i in invoice.line_items), start=Decimal("0"))
        return AgentResult(
            agent=AgentName.INVOICE_GENERATOR,
            reply=reply,
            planned_steps=["parse_invoice", "structure_fields", "pdf_endpoint"],
            metadata={
                "invoice_ref": inv_id,
                "parsed_items_count": str(len(invoice.line_items)),
                "parsed_total": f"{total:.2f}",
                "source": source,
                "currency": invoice.currency,
                "invoice_payload": invoice.model_dump_json(),
                "invoice_actions": "pdf,csv",
            },
        )

    upload_hint = (
        "Upload a PDF or image invoice in Settings → Invoice import, or paste line items like:\n"
        "  1200 Website design\n  400 SEO audit"
    )
    reply = (
        "[AGENT: INVOICE]\n\n"
        f"I could not detect invoice line items in your message. {upload_hint}\n\n"
        '{"intent":"create_invoice","steps":["Upload PDF/image or paste line items","Review structured output","Generate PDF"],'
        '"tools_needed":["parse_invoice_upload","render_invoice_pdf"],"notes":"no line items detected"}'
    )
    return AgentResult(
        agent=AgentName.INVOICE_GENERATOR,
        reply=reply,
        planned_steps=["collect_line_items", "parse_upload", "pdf_endpoint"],
        metadata={"invoice_ref": inv_id, "parsed_items_count": "0", "source": "prompt"},
    )
