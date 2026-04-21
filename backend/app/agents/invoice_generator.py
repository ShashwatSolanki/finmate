"""Invoice Generator agent — parse invoice lines, prompt concrete draft + PDF handoff."""

import re
import uuid
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy.orm import Session

from app.agents.types import AgentName, AgentResult
from app.ml.finmate import generate

_AMOUNT_LINE = re.compile(r"^\s*([\d.,]+)\s+(.+?)\s*$", re.M)


def run(
    user_id: UUID,
    message: str,
    db: Session,
    rag_context: str | None = None,
) -> AgentResult:
    _ = db
    lines = []
    parsed_items: list[dict[str, str]] = []
    total = Decimal("0")
    for m in _AMOUNT_LINE.finditer(message):
        amt, desc = m.group(1), m.group(2).strip()
        normalized = amt.replace(",", "")
        try:
            val = Decimal(normalized)
        except InvalidOperation:
            continue
        if val <= 0:
            continue
        lines.append(f"  - {desc}: {amt}")
        parsed_items.append({"description": desc, "amount": f"{val:.2f}"})
        total += val

    inv_id = str(uuid.uuid4())[:8].upper()

    rag_block = ""
    if rag_context and rag_context.strip():
        rag_block = "\n\n[Past context]\n" + rag_context.strip()[:2000]

    if lines:
        line_items_text = (
            "Line items parsed:\n"
            + "\n".join(lines)
            + f"\nComputed total: {total:.2f}\n"
            + "When replying, include a ready-to-send JSON body for POST /api/invoices/pdf "
            + "with line_items and currency."
        )
    else:
        line_items_text = (
            "No line items detected — user can add lines like `99.00 Web design`.\n"
            "Ask for missing client name, line items, and currency before PDF generation."
        )

    enriched = (
        f"{message}\n\n"
        f"[Invoice context]\n"
        f"Invoice ref: #{inv_id}\n"
        f"Client user ID: {user_id}\n"
        f"{line_items_text}"
        f"{rag_block}"
    )

    try:
        reply = generate(enriched)
    except Exception:
        if parsed_items:
            items_json = ", ".join(
                f'{{"description":"{x["description"]}","amount":"{x["amount"]}"}}' for x in parsed_items[:8]
            )
            notes = (
                "I parsed your line items and prepared a ready body for invoice PDF generation. "
                "Use this payload with POST /api/invoices/pdf."
            )
            reply = (
                "[AGENT: INVOICE]\n\n"
                f"{notes}\n\n"
                '{"intent":"create_invoice","steps":["Confirm line items","Post payload to /api/invoices/pdf","Download generated PDF"],'
                '"tools_needed":["render_invoice_pdf"],"notes":"fallback response"}\n'
                f'\nExample payload: {{"line_items":[{items_json}],"currency":"USD"}}'
            )
        else:
            reply = (
                "[AGENT: INVOICE]\n\n"
                "Share line items in this format: `1200 Website design` on separate lines, then I will prepare a PDF-ready payload.\n\n"
                '{"intent":"create_invoice","steps":["Collect line items","Compute totals","Generate invoice PDF"],'
                '"tools_needed":["render_invoice_pdf"],"notes":"fallback response"}'
            )

    return AgentResult(
        agent=AgentName.INVOICE_GENERATOR,
        reply=reply,
        planned_steps=["parse_line_items", "finmate_generate", "pdf_endpoint"],
        metadata={
            "invoice_ref": inv_id,
            "parsed_items_count": str(len(parsed_items)),
            "parsed_total": f"{total:.2f}",
        },
    )