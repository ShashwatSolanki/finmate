"""Invoice Generator — parse text/PDF-style content, structured output, PDF handoff."""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.types import AgentName, AgentResult
from app.db.models import MemoryChunk, Transaction
from app.config import settings
from app.invoice.parse_invoice import parse_invoice_text
from app.invoice.schemas import ParsedLineItem, StructuredInvoice, TaxBreakdownItem
from app.ml.finmate import generate

_AMOUNT_LINE = re.compile(
    r"^\s*(?:[-*]\s*)?(.+?)\s+(?:₹|Rs\.?|INR|USD|EUR|GBP|\$|€|£)?\s*"
    r"((?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3}|\d+)(?:\.\d{1,2})?)\s*[.!]?\s*$",
    re.I,
)

_AMOUNT_FIRST_LINE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:₹|Rs\.?|INR|USD|EUR|GBP|\$|€|£)?\s*"
    r"((?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3}|\d+)(?:\.\d{1,2})?)\s+(.+?)\s*[.!]?\s*$",
    re.I,
)

_EXPLICIT_ITEM_CLAUSE = re.compile(
    r"(?P<description>[A-Z][^,:;\n.]{1,100}?)\s*:?\s*"
    r"(?P<quantity>\d+(?:\.\d+)?)\s*(?:units?|items?)?\s*(?:at|@|x|×)\s*"
    r"(?:(?:INR|Rs\.?|USD|EUR|GBP|₹|\$|€|£)\s*)?"
    r"(?P<unit_price>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)"
    r"\s*(?:each|per\s+(?:unit|item))?",
    re.I,
)


def _extract_original_request(message: str) -> str:
    """Remove verified specialist observations before parsing invoice input."""
    marker = "\n\n[Verified specialist observations]"
    if marker in message:
        return message.split(marker, 1)[0].strip()
    return message.strip()


def _wants_invoice_output(request: str) -> bool:
    """Only offer a generated artifact when the user actually asks to create/export one."""
    return bool(
        re.search(
            r"\b(?:create|generate|make|draft|prepare|produce|build)\b.{0,60}"
            r"\b(?:an?\s+)?(?:client\s+)?invoice\b",
            request,
            re.I,
        )
        or re.search(r"\b(?:export|download|render)\b.{0,35}\b(?:invoice|pdf|csv)\b", request, re.I)
        or re.search(r"\b(?:create|generate|make|prepare|produce|render)\b.{0,40}\b(?:pdf|csv)\b", request, re.I)
        or re.search(r"\bpdf\s+and\s+csv\s+export\b", request, re.I)
    )


def _wants_expense_invoice(request: str) -> bool:
    """Require explicit invoice-generation intent before creating an expense PDF."""
    return bool(
        re.search(
            r"\b(?:generate|create|make|draft|prepare|produce|build)\b.{0,55}"
            r"\b(?:an?\s+)?(?:expense\s+)?invoice\b",
            request,
            re.I,
        )
        or re.search(r"\b(?:expense|spending|transaction)\s+invoice\b", request, re.I)
        or re.search(r"\binvoice[- ]style\s+report\b", request, re.I)
        or re.search(
            r"\binvoice\b.{0,35}\b(?:for|from)\b.{0,30}\b(?:my|our|recent|recorded)\s+"
            r"(?:expenses?|spending|transactions?)\b",
            request,
            re.I,
        )
    )


def _detect_currency(message: str) -> str:
    """Infer an explicit currency marker; default to USD only when none is supplied."""
    text = message.upper()
    if "₹" in message or re.search(r"\bINR\b|\bRS\.?\s*", text):
        return "INR"
    if "€" in message or re.search(r"\bEUR\b", text):
        return "EUR"
    if "£" in message or re.search(r"\bGBP\b", text):
        return "GBP"
    if "¥" in message or re.search(r"\bJPY\b", text):
        return "JPY"
    if "$" in message or re.search(r"\bUSD\b", text):
        return "USD"
    return "USD"


def _parse_simple_lines(message: str) -> list[dict[str, str]]:
    """Parse compact natural-language invoice requests into line items."""
    cleaned = re.sub(
        r"^\s*(?:(?:create|generate|make|draft|prepare)\s+(?:an?\s+)?(?:client\s+)?invoice"
        r"|i\s+need\s+(?:an?\s+)?(?:client\s+)?invoice)"
        r"\s*(?:for|from|line\s+items?)?\s*[:\-]?\s*",
        "",
        message,
        flags=re.I,
    )
    # Commas/semicolons separate compact line items. Keep "and" within a
    # description (for example, "design and development") unless the preceding
    # segment is already a complete description+amount pair.
    comma_parts = re.split(r",(?!\d)|;", cleaned, flags=re.I)
    conjunction_parts: list[str] = []
    for comma_part in comma_parts:
        and_parts = re.split(r"\s+and\s+", comma_part, flags=re.I)
        current = and_parts[0]
        for next_part in and_parts[1:]:
            current_is_line_item = bool(
                _AMOUNT_LINE.match(current.strip())
                or _AMOUNT_FIRST_LINE.match(current.strip())
            )
            next_is_invoice_request = bool(
                re.match(
                    r"^\s*(?:(?:create|generate|make|draft|prepare)\s+(?:an?\s+)?(?:client\s+)?invoice|"
                    r"i\s+need\s+(?:an?\s+)?(?:client\s+)?invoice)\b",
                    next_part,
                    re.I,
                )
            )
            if current_is_line_item or next_is_invoice_request:
                conjunction_parts.append(current)
                current = next_part
            else:
                current = f"{current} and {next_part}"
        conjunction_parts.append(current)

    parts: list[str] = []
    for conjunction_part in conjunction_parts:
        plus_parts = re.split(r"\s+plus\s+", conjunction_part, flags=re.I)
        if len(plus_parts) > 1 and all(
            _AMOUNT_LINE.match(part.strip()) or _AMOUNT_FIRST_LINE.match(part.strip())
            for part in plus_parts
        ):
            parts.extend(plus_parts)
        else:
            parts.append(conjunction_part)
    
    items: list[dict[str, str]] = []
    for part in parts:
        text = part.strip().rstrip(".!?")
        if not text:
            continue
        m = _AMOUNT_LINE.match(text)
        if m:
            desc, amt = m.groups()
        else:
            m = _AMOUNT_FIRST_LINE.match(text)
            if not m:
                continue
            amt, desc = m.groups()
        try:
            val = Decimal(amt.replace(",", ""))
        except InvalidOperation:
            continue
        if val <= 0:
            continue
        desc = re.sub(r"^(?:for|of)\s+", "", desc.strip(), flags=re.I)
        desc = re.sub(
            r"^(?:and\s+)?(?:(?:create|generate|make|draft|prepare)\s+(?:an?\s+)?(?:client\s+)?invoice"
            r"(?:\s+(?:for|from|line\s+items?))?|invoice\s+(?:a\s+)?client\s+for)\s*",
            "",
            desc,
            flags=re.I,
        )
        if desc:
            items.append({"description": desc, "amount": f"{val:.2f}"})
    return items


def _count_explicit_item_clauses(message: str) -> int:
    """Count quantity/unit-price clauses so a partially parsed invoice is never exported."""
    pattern = re.compile(
        r"\b\d+(?:\.\d+)?\s*(?:units?|items?)?\s*(?:at|@|x|×)\s*"
        r"(?:(?:INR|Rs\.?|USD|EUR|GBP|₹|\$|€|£)\s*)?"
        r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?",
        re.I,
    )
    return sum(1 for _ in pattern.finditer(message))


def _explicit_items_from_request(message: str) -> list[ParsedLineItem]:
    """Parse explicit 'Item: 2 units at INR 1,200 each' clauses without an LLM."""
    items: list[ParsedLineItem] = []
    for match in _EXPLICIT_ITEM_CLAUSE.finditer(message):
        description = re.sub(
            r"^(?:(?:and|add|include|plus)\s+)+",
            "",
            match.group("description").strip(),
            flags=re.I,
        )
        if not description:
            continue
        try:
            quantity = Decimal(match.group("quantity"))
            unit_price = Decimal(match.group("unit_price").replace(",", ""))
        except InvalidOperation:
            continue
        if quantity <= 0 or unit_price <= 0:
            continue
        amount = (quantity * unit_price).quantize(Decimal("0.01"))
        items.append(
            ParsedLineItem(
                description=description,
                quantity=float(quantity),
                unit_price=unit_price,
                amount=amount,
            )
        )
    # Each regex match corresponds to one requested line item. Preserve repeated
    # clauses: two identical products can legitimately appear as separate invoice rows.
    return items


def _tax_from_rate(message: str, subtotal: Decimal) -> Decimal | None:
    """Calculate tax only when the request explicitly states a percentage."""
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*%\s*(?:tax|gst|vat)\b", message, re.I)
    if not match:
        match = re.search(r"\b(?:tax|gst|vat)\s+(?:at|of)\s*(\d+(?:\.\d+)?)\s*%", message, re.I)
    if not match:
        return None
    try:
        rate = Decimal(match.group(1))
    except InvalidOperation:
        return None
    if rate < 0 or rate > 100:
        return None
    return (subtotal * rate / Decimal("100")).quantize(Decimal("0.01"))


def _extract_parties_and_due_date(message: str) -> tuple[str | None, str | None, str | None]:
    vendor = None
    bill_to = None
    invoice_context = re.search(
        r"\b(?:create|generate|make|draft|prepare)\s+(?:an?\s+)?(?:client\s+)?invoice\b(.*)",
        message,
        re.I | re.S,
    )
    parties_text = invoice_context.group(1) if invoice_context else message
    parties = re.search(
        r"\bfrom\s+(.+?)\s+to\s+(.+?)(?=:|,\s*(?:due|dated|invoice date)\b|;|\n|$)",
        parties_text,
        re.I,
    )
    if parties:
        vendor = parties.group(1).strip(" ,.")
        bill_to = parties.group(2).strip(" ,.")
    due = None
    due_days = re.search(r"\bdue\s+(\d{1,3})\s+days?\s+from\s+today\b", message, re.I)
    if due_days:
        due = (date.today() + timedelta(days=int(due_days.group(1)))).isoformat()
    return vendor, bill_to, due


def _references_uploaded_invoice(request: str) -> bool:
    """Recognize a direct reference to an upload, not an incidental mention."""
    return bool(
        re.search(
            r"\b(?:invoice|receipt|bill|document)\b.{0,55}\b(?:uploaded|attached|imported|above|earlier)\b",
            request,
            re.I,
        )
        or re.search(
            r"\b(?:use|using|based on|review|inspect|extract|read|check|audit|reconcile|"
            r"look at|show me|tell me|what is|what's)\s+"
            r"(?:(?:the|my|this|that)\s+)?"
            r"(?:(?:uploaded|attached|imported)\s+)?"
            r"(?:invoice|receipt|bill|document|image|file)\b",
            request,
            re.I,
        )
        or re.search(
            r"\bseparat(?:e|ely|ing)\b.{0,90}"
            r"\b(?:uploaded|attached|imported)\b.{0,30}\b(?:invoice|receipt|bill|document)\b",
            request,
            re.I,
        )
        or re.search(
            r"\b(?:identify|distinguish|separate|show|report|list|tell me)\b.{0,180}"
            r"\b(?:from|in|on)\s+(?:(?:my|the|this|that)\s+)?"
            r"(?:(?:previously|already)\s+)?(?:uploaded|attached|imported)\s+"
            r"(?:invoice|receipt|bill|document)\b",
            request,
            re.I,
        )
    )


def _invoice_from_memory_text(text: str) -> StructuredInvoice | None:
    """Rebuild a structured invoice from the bounded memory text of a saved upload."""
    if "uploaded invoice file:" not in text.casefold():
        return None

    def field(label: str) -> str | None:
        found = re.search(rf"(?im)^\s*{re.escape(label)}\s*:\s*(.+?)\s*$", text)
        return found.group(1).strip() if found else None

    items: list[ParsedLineItem] = []
    extraction_warnings: list[str] = []
    in_warnings = False
    in_items = False
    stop_labels = re.compile(r"^(?:subtotal|tax|total|notes?)\s*:", re.I)
    for line in text.splitlines():
        clean = line.strip()
        if clean.casefold() == "extraction warnings:":
            in_warnings = True
            continue
        if in_warnings:
            if clean.startswith("- "):
                extraction_warnings.append(clean[2:].strip())
                continue
            if clean:
                in_warnings = False
        if clean.casefold() == "line items:":
            in_items = True
            continue
        if in_items and stop_labels.match(clean):
            in_items = False
        if not in_items or not clean.startswith("-"):
            continue
        match = re.match(
            r"^\s*-\s*(.+):\s*([\d,]+(?:\.\d{1,2})?)\s*(INR|USD|EUR|GBP|CAD|AUD|JPY|CHF|SGD)?\s*$",
            clean,
            re.I,
        )
        if not match:
            continue
        description, amount_raw, _currency = match.groups()
        try:
            amount = Decimal(amount_raw.replace(",", ""))
        except InvalidOperation:
            continue
        if amount <= 0:
            continue
        items.append(ParsedLineItem(description=description.strip(), amount=amount))

    if not items:
        return None

    currency = (field("Currency") or _detect_currency(text)).upper()

    def amount_field(label: str) -> Decimal | None:
        raw = field(label)
        if raw is None:
            return None
        cleaned = re.sub(
            r"(?i)\b(?:INR|USD|EUR|GBP|CAD|AUD|JPY|CHF|SGD)\b|[₹$€£¥,]",
            "",
            raw,
        ).strip()
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None

    subtotal_value = amount_field("Subtotal")
    tax = amount_field("Tax")
    total_value = amount_field("Total")
    subtotal = subtotal_value if subtotal_value is not None else sum(
        (item.amount for item in items), start=Decimal("0")
    )
    tax_breakdown: list[TaxBreakdownItem] = []
    for line in text.splitlines():
        match = re.match(
            r"^\s*-\s*((?:CGST|SGST|IGST)(?:\s+\d+(?:\.\d+)?\s*%)?)\s*:\s*"
            r"([\d,]+(?:\.\d{1,2})?)\s*(INR|USD|EUR|GBP|CAD|AUD|JPY|CHF|SGD)?\s*$",
            line.strip(),
            re.I,
        )
        if not match:
            continue
        label, amount_raw, _currency = match.groups()
        try:
            component_amount = Decimal(amount_raw.replace(",", ""))
        except InvalidOperation:
            continue
        if component_amount >= 0:
            tax_breakdown.append(TaxBreakdownItem(label=label.strip(), amount=component_amount))
    if tax is None and tax_breakdown:
        tax = sum((component.amount for component in tax_breakdown), start=Decimal("0"))
    total = total_value if total_value is not None else subtotal + (tax or Decimal("0"))

    notes_parts = [field("Notes")] if field("Notes") else []
    if extraction_warnings:
        notes_parts.append("OCR extraction warnings: " + "; ".join(extraction_warnings[:10]))

    return StructuredInvoice(
        invoice_number=field("Invoice number"),
        invoice_date=field("Invoice date"),
        due_date=field("Due date"),
        vendor_name=field("Vendor"),
        bill_to=field("Bill to"),
        currency=currency,
        line_items=items,
        subtotal=subtotal,
        tax=tax,
        tax_breakdown=tax_breakdown,
        total=total,
        notes="\n".join(notes_parts) if notes_parts else None,
    )


def _invoice_from_recent_upload(db: Session, user_id: UUID) -> StructuredInvoice | None:
    """Load the most recent parsable saved invoice when the user refers to an upload."""
    try:
        rows = db.scalars(
            select(MemoryChunk)
            .where(
                MemoryChunk.user_id == user_id,
                MemoryChunk.source == "chat",
                MemoryChunk.content.ilike("%Uploaded invoice file:%"),
            )
            .order_by(MemoryChunk.created_at.desc(), MemoryChunk.id.desc())
            .limit(10)
        ).all()
    except Exception:
        return None
    if not isinstance(rows, (list, tuple)):
        return None
    for row in rows:
        memory_text = getattr(row, "content", None)
        if not isinstance(memory_text, str):
            continue
        invoice = _invoice_from_memory_text(memory_text)
        if invoice is not None:
            return invoice
    return None


def _structured_from_message(message: str) -> StructuredInvoice | None:
    """Parse explicit item clauses first, then pasted OCR/PDF text and simple lines."""
    explicit_items = _explicit_items_from_request(message)
    expected_item_clauses = _count_explicit_item_clauses(message)
    if expected_item_clauses and len(explicit_items) != expected_item_clauses:
        # Do not fall through to a looser parser that could export only a subset
        # of a multi-item natural-language request.
        return None
    if explicit_items:
        subtotal = sum((item.amount for item in explicit_items), start=Decimal("0"))
        tax = _tax_from_rate(message, subtotal)
        vendor, bill_to, due_date = _extract_parties_and_due_date(message)
        return StructuredInvoice(
            vendor_name=vendor,
            bill_to=bill_to,
            due_date=due_date,
            currency=_detect_currency(message),
            line_items=explicit_items,
            subtotal=subtotal,
            tax=tax,
            total=subtotal + (tax or Decimal("0")),
        )

    simple = _parse_simple_lines(message)
    explicit_multi_item_plus = bool(re.search(r"\bplus\b", message, re.I)) and len(simple) >= 2
    explicit_client_invoice = bool(
        re.search(r"\binvoice\s+(?:a\s+)?client\s+for\b", message, re.I)
    )
    if simple and (
        len(message.strip()) < 80
        or explicit_multi_item_plus
        or explicit_client_invoice
    ):
        line_items = [ParsedLineItem(description=x["description"], amount=Decimal(x["amount"])) for x in simple]
        total = sum((i.amount for i in line_items), start=Decimal("0"))
        return StructuredInvoice(
            line_items=line_items,
            subtotal=total,
            total=total,
            currency=_detect_currency(message),
        )

    result = parse_invoice_text(message, source_type="text", filename="chat-message.txt")
    if result.invoice.line_items:
        return result.invoice

    if simple:
        line_items = [ParsedLineItem(description=x["description"], amount=Decimal(x["amount"])) for x in simple]
        total = sum((i.amount for i in line_items), start=Decimal("0"))
        return StructuredInvoice(
            line_items=line_items,
            subtotal=total,
            total=total,
            currency=_detect_currency(message) if _detect_currency(message) != "USD" else result.invoice.currency,
        )
    return None


def _expense_reporting_window(request: str | None) -> tuple[date, date, str]:
    """Resolve the requested period for expense-backed invoice summaries."""
    today = date.today()
    if request and re.search(
        r"\b(?:previous|prior|last|most recent complete)\s+(?:full\s+)?calendar\s+month\b|"
        r"\bmonth before (?:it|this)\b",
        request,
        re.I,
    ):
        this_month_start = date(today.year, today.month, 1)
        end = this_month_start - timedelta(days=1)
        start = date(end.year, end.month, 1)
        label = f"previous calendar month ({start.isoformat()} to {end.isoformat()})"
        return start, end, label
    start = today - timedelta(days=30)
    return start, today, f"last 30 days ({start.isoformat()} to {today.isoformat()})"


def _expense_invoice_from_transactions(
    db: Session,
    user_id: UUID,
    request: str | None = None,
) -> StructuredInvoice | None:
    """Build an exportable invoice-style expense summary from recorded transactions."""
    start, end, period_label = _expense_reporting_window(request)
    rows = db.scalars(
        select(Transaction)
        .where(
            Transaction.user_id == user_id,
            Transaction.occurred_on >= start,
            Transaction.occurred_on <= end,
            Transaction.amount < 0,
        )
        .order_by(Transaction.occurred_on.desc(), Transaction.created_at.desc())
        .limit(50)
    ).all()
    if not rows:
        return None

    # An invoice has one currency. Never add amounts denominated in different currencies.
    currency = (rows[0].currency or "USD").upper()
    items = [
        ParsedLineItem(
            description=(row.description or row.category or "Expense")[:500],
            amount=abs(row.amount),
        )
        for row in rows
        if row.amount != 0 and (row.currency or "USD").upper() == currency
    ]
    if not items:
        return None

    subtotal = sum((item.amount for item in items), start=Decimal("0"))
    return StructuredInvoice(
        invoice_number=f"EXP-{date.today().strftime('%Y%m%d')}",
        invoice_date=date.today().isoformat(),
        vendor_name="FinMate Expense Summary",
        currency=currency,
        line_items=items,
        subtotal=subtotal,
        total=subtotal,
        notes=(
            f"Generated from expense transactions in {currency} over {period_label}. "
            "Transactions in other currencies were excluded to avoid mixing currencies."
            if len({(row.currency or "USD").upper() for row in rows}) > 1
            else f"Generated from expense transactions in this currency over {period_label}."
        ),
    )


def _format_reply(
    invoice: StructuredInvoice,
    inv_id: str,
    *,
    source_note: str,
    allow_export: bool,
) -> str:
    lines = [
        f"Invoice ref: #{inv_id}",
    ]
    if invoice.vendor_name:
        lines.append(f"Vendor: {invoice.vendor_name}")
    if invoice.bill_to:
        lines.append(f"Bill to: {invoice.bill_to}")
    if invoice.invoice_date:
        lines.append(f"Date: {invoice.invoice_date}")
    if invoice.due_date:
        lines.append(f"Due date: {invoice.due_date}")
    lines.append("")
    lines.append("Line items:")
    for li in invoice.line_items[:12]:
        qty = f" ({li.quantity} x {li.unit_price})" if li.quantity and li.unit_price else ""
        lines.append(f"  - {li.description}{qty}: {li.amount:.2f} {invoice.currency}")
    if invoice.subtotal is not None:
        lines.append(f"Subtotal: {invoice.subtotal:.2f} {invoice.currency}")
    if invoice.tax is not None:
        tax_label = "Total tax" if invoice.tax_breakdown else "Tax"
        lines.append(f"{tax_label}: {invoice.tax:.2f} {invoice.currency}")
    if invoice.tax_breakdown:
        lines.extend(
            f"{component.label}: {component.amount:.2f} {invoice.currency}"
            for component in invoice.tax_breakdown
        )
    if invoice.total is not None:
        lines.append(f"Total: {invoice.total:.2f} {invoice.currency}")
    if invoice.notes:
        lines.append(f"Notes: {invoice.notes}")

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
        "tax_breakdown": [
            {"label": component.label, "amount": str(component.amount)}
            for component in invoice.tax_breakdown
        ],
        "total": str(invoice.total) if invoice.total is not None else None,
        "notes": invoice.notes,
    }

    if allow_export:
        prose = (
            "Structured invoice data extracted. Review the fields below, then generate a PDF from Settings "
            "(Invoice import) or POST /api/invoices/pdf/structured with this payload.\n\n"
            + "\n".join(lines)
        )
        intent = "create_invoice"
        steps = ["Review parsed fields", "Edit in Settings if needed", "Download PDF"]
        tools_needed = ["parse_invoice_upload", "render_invoice_pdf"]
    else:
        document_note = (
            "\n\nThe uploaded invoice/receipt is document evidence only: it is separate from recorded transactions "
            "and does not establish that payment was made. It has not been added to transaction totals."
            if "previously uploaded invoice" in source_note
            else ""
        )
        prose = (
            "Extracted invoice/receipt details (no new PDF was requested)."
            + document_note
            + "\n\n"
            + "\n".join(lines)
        )
        intent = "review_invoice"
        steps = ["Review extracted fields", "Correct any OCR errors"]
        tools_needed = ["parse_invoice_upload"]

    json_tail = json.dumps(
        {
            "intent": intent,
            "steps": steps,
            "tools_needed": tools_needed,
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
    request_text = _extract_original_request(message)
    request = request_text.lower()
    wants_invoice_output = _wants_invoice_output(request_text)
    expected_item_clauses = _count_explicit_item_clauses(request_text)
    # An explicit new multi-item invoice request takes priority over unrelated
    # receipt references elsewhere in a combined prompt. Do not accidentally
    # export the previously uploaded receipt instead of the invoice the user asked for.
    new_invoice_from_text = wants_invoice_output and expected_item_clauses > 0
    if new_invoice_from_text:
        invoice = _structured_from_message(request_text)
        invoice_from_uploaded_context = False
    else:
        wants_uploaded_invoice = _references_uploaded_invoice(request_text)
        invoice = _invoice_from_recent_upload(db, user_id) if wants_uploaded_invoice else None
        invoice_from_uploaded_context = invoice is not None
        if invoice is None:
            invoice = _structured_from_message(request_text)
    if invoice is None and wants_invoice_output and expected_item_clauses > 0:
        reply = (
            "[AGENT: INVOICE]\n\n"
            "I couldn't safely parse every quantity/unit-price item in your request, so I have not created "
            "or exported a partial invoice. Please clarify the missing item description, quantity, and unit price, "
            "then try again.\n\n"
            '{"intent":"invoice_parse_incomplete","steps":["Compare requested item clauses","Clarify missing item data","Rebuild invoice totals"],'
            '"tools_needed":[],"notes":"partial invoice export blocked"}'
        )
        return AgentResult(
            agent=AgentName.INVOICE_GENERATOR,
            reply=reply,
            planned_steps=["count_requested_items", "validate_parsed_item_count", "block_partial_export"],
            metadata={
                "source": "incomplete_invoice_request",
                "parsed_items_count": "0",
                "expected_item_clauses": str(expected_item_clauses),
            },
        )
    wants_expense_invoice = _wants_expense_invoice(request)
    invoice_from_transactions = False
    if invoice is None and wants_expense_invoice:
        invoice = _expense_invoice_from_transactions(db, user_id, request_text)
        invoice_from_transactions = invoice is not None

    rag_block = ""
    if rag_context and rag_context.strip():
        rag_block = "\n\n[Past context]\n" + rag_context.strip()[:2000]

    if invoice and invoice.line_items:
        # The invoice reference shown in chat must be the same identifier serialized
        # into the structured payload and consumed by both PDF and CSV export.
        # Preserve a source invoice number when reviewing an uploaded invoice.
        if invoice.invoice_number and invoice.invoice_number.strip():
            inv_id = invoice.invoice_number.strip().lstrip("#").upper()[:24]
        else:
            invoice.invoice_number = inv_id

        # Invoice generation is intentionally deterministic here. The structured
        # invoice object is the source of truth for PDF/CSV export artifacts; using
        # the general LLM response path can discard those machine-readable fields.
        reply = _format_reply(
            invoice,
            inv_id,
            source_note=(
                "generated from recent user transactions"
                if invoice_from_transactions
                else "reconstructed from a previously uploaded invoice saved in this conversation"
                if invoice_from_uploaded_context
                else "parsed from message text"
            ),
            allow_export=wants_invoice_output,
        )
        source = (
            "transaction_summary"
            if invoice_from_transactions
            else "uploaded_invoice_context"
            if invoice_from_uploaded_context
            else "structured_parse"
        )

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
                **(
                    {
                        "invoice_payload": invoice.model_dump_json(),
                        "invoice_actions": "pdf,csv",
                    }
                    if wants_invoice_output
                    else {}
                ),
            },
        )

    if wants_expense_invoice:
        _period_start, _period_end, period_label = _expense_reporting_window(request_text)
        reply = (
            "[AGENT: INVOICE]\n\n"
            f"I couldn't prepare an expense summary because there are no matching recorded expense transactions "
            f"for {period_label}. Uploaded invoices and receipts are separate from transaction history; import "
            "transactions first if you want them included.\n\n"
            '{"intent":"expense_summary_unavailable","steps":["Check recent recorded transactions","Import expenses if needed"],'
            '"tools_needed":["list_transactions"],"notes":"no matching recorded expenses"}'
        )
        return AgentResult(
            agent=AgentName.INVOICE_GENERATOR,
            reply=reply,
            planned_steps=["query_recent_expenses", "report_missing_transaction_data"],
            metadata={
                "invoice_ref": inv_id,
                "parsed_items_count": "0",
                "source": "missing_transaction_history",
                "period_start": _period_start.isoformat(),
                "period_end": _period_end.isoformat(),
            },
        )

    upload_hint = (
        "Upload a PDF or image invoice in Settings → Invoice import, paste line items like:\n"
        "  1200 Website design\n  400 SEO audit, or import transactions first if you want an expense summary."
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
