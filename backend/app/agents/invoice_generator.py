"""Invoice Generator agent — structured text + FinMate NL response."""

import re
import uuid
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
    for m in _AMOUNT_LINE.finditer(message):
        amt, desc = m.group(1), m.group(2).strip()
        lines.append(f"  - {desc}: {amt}")

    inv_id = str(uuid.uuid4())[:8].upper()

    rag_block = ""
    if rag_context and rag_context.strip():
        rag_block = "\n\n[Past context]\n" + rag_context.strip()[:2000]

    if lines:
        line_items_text = "Line items parsed:\n" + "\n".join(lines)
    else:
        line_items_text = "No line items detected — user can add lines like `99.00 Web design`."

    enriched = (
        f"{message}\n\n"
        f"[Invoice context]\n"
        f"Invoice ref: #{inv_id}\n"
        f"Client user ID: {user_id}\n"
        f"{line_items_text}"
        f"{rag_block}"
    )

    reply = generate(enriched)

    return AgentResult(
        agent=AgentName.INVOICE_GENERATOR,
        reply=reply,
        planned_steps=["parse_line_items", "finmate_generate", "pdf_endpoint"],
        metadata={"invoice_ref": inv_id},
    )