import json
import re
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import ChatMessage, ChatSession, MemoryChunk, User
from app.db.session import get_db
from app.invoice.schemas import StructuredInvoice

router = APIRouter()


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0

    model_config = {"from_attributes": True}


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class InvoiceImportCreate(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    source_type: Literal["pdf", "image", "csv"]
    confidence: Literal["high", "medium", "low"]
    invoice: StructuredInvoice
    warnings: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("warnings")
    @classmethod
    def bound_warnings(cls, warnings: list[str]) -> list[str]:
        return [warning.strip()[:300] for warning in warnings if warning.strip()][:10]

    @field_validator("invoice")
    @classmethod
    def limit_invoice_line_items(cls, invoice: StructuredInvoice) -> StructuredInvoice:
        if len(invoice.line_items) > 100:
            raise ValueError("An invoice can contain at most 100 line items.")
        return invoice


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    agent: str | None
    metadata: dict[str, str] | None = None
    created_at: datetime

    @classmethod
    def from_row(cls, row: ChatMessage) -> "MessageOut":
        raw = row.metadata_
        metadata = {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else None
        return cls(
            id=row.id,
            role=row.role,
            content=row.content,
            agent=row.agent,
            metadata=metadata,
            created_at=row.created_at,
        )


class ImportedTransactionPreview(BaseModel):
    amount: str = Field(..., min_length=1, max_length=48)
    currency: str = Field(..., min_length=1, max_length=8)
    category: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    occurred_on: str = Field(..., min_length=1, max_length=32)


class TransactionCsvImportCreate(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    imported_count: int = Field(..., ge=0, le=20000)
    skipped_count: int = Field(default=0, ge=0, le=20000)
    preview: list[ImportedTransactionPreview] = Field(default_factory=list, max_length=8)
    sample_errors: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("sample_errors")
    @classmethod
    def bound_csv_errors(cls, errors: list[str]) -> list[str]:
        return [error.strip()[:300] for error in errors if error.strip()][:10]


def _session_for_user(db: Session, user_id: uuid.UUID, session_id: uuid.UUID) -> ChatSession:
    row = db.scalar(
        select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user_id)
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return row


def _title_from_message(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip())
    if not cleaned:
        return "New chat"
    return cleaned[:60] + ("…" if len(cleaned) > 60 else "")


def _invoice_memory_text(body: InvoiceImportCreate) -> str:
    invoice = body.invoice
    lines = [
        f"Uploaded invoice file: {body.filename}",
        f"Source type: {body.source_type}; extraction confidence: {body.confidence}.",
    ]
    if invoice.invoice_number:
        lines.append(f"Invoice number: {invoice.invoice_number}")
    if invoice.invoice_date:
        lines.append(f"Invoice date: {invoice.invoice_date}")
    if invoice.due_date:
        lines.append(f"Due date: {invoice.due_date}")
    if invoice.vendor_name:
        lines.append(f"Vendor: {invoice.vendor_name}")
    if invoice.bill_to:
        lines.append(f"Bill to: {invoice.bill_to}")
    if invoice.line_items:
        lines.append("Line items:")
        lines.extend(
            f"- {item.description}: {item.amount} {invoice.currency}"
            for item in invoice.line_items
        )
    if invoice.subtotal is not None:
        lines.append(f"Subtotal: {invoice.subtotal} {invoice.currency}")
    if invoice.tax is not None:
        lines.append(f"Tax: {invoice.tax} {invoice.currency}")
    if invoice.total is not None:
        lines.append(f"Total: {invoice.total} {invoice.currency}")
    if invoice.notes:
        lines.append(f"Notes: {invoice.notes}")
    if body.warnings:
        lines.append("Extraction warnings:")
        lines.extend(f"- {warning}" for warning in body.warnings[:10])
    return "\n".join(lines)[:3500]


@router.get("", response_model=list[ConversationOut])
def list_conversations(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> list[ConversationOut]:
    rows = db.execute(
        select(
            ChatSession,
            func.count(ChatMessage.id).label("message_count"),
        )
        .outerjoin(ChatMessage, ChatMessage.session_id == ChatSession.id)
        .where(ChatSession.user_id == current.id)
        .group_by(ChatSession.id)
        .order_by(ChatSession.updated_at.desc())
    ).all()
    return [
        ConversationOut(
            id=s.id,
            title=s.title,
            created_at=s.created_at,
            updated_at=s.updated_at,
            message_count=int(count or 0),
        )
        for s, count in rows
    ]


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
def create_conversation(
    body: ConversationCreate,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> ConversationOut:
    row = ChatSession(user_id=current.id, title=body.title or "New chat")
    db.add(row)
    db.commit()
    db.refresh(row)
    return ConversationOut(
        id=row.id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
        message_count=0,
    )


@router.patch("/{session_id}", response_model=ConversationOut)
def update_conversation(
    session_id: uuid.UUID,
    body: ConversationUpdate,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> ConversationOut:
    row = _session_for_user(db, current.id, session_id)
    row.title = body.title.strip()
    db.commit()
    db.refresh(row)
    count = db.scalar(
        select(func.count(ChatMessage.id)).where(ChatMessage.session_id == row.id)
    )
    return ConversationOut(
        id=row.id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
        message_count=int(count or 0),
    )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> None:
    row = _session_for_user(db, current.id, session_id)
    db.delete(row)
    db.commit()


def _transaction_csv_import_memory_text(body: TransactionCsvImportCreate) -> str:
    lines = [
        f"Imported transactions CSV file: {body.filename}",
        f"Rows imported: {body.imported_count}; rows skipped: {body.skipped_count}.",
    ]
    if body.preview:
        lines.append("Imported transaction preview:")
        lines.extend(
            f"- {row.occurred_on}: {row.description or row.category or 'Transaction'} "
            f"{row.amount} {row.currency}"
            for row in body.preview
        )
    if body.sample_errors:
        lines.append("Sample import errors:")
        lines.extend(f"- {error}" for error in body.sample_errors)
    return "\\n".join(lines)[:3500]


@router.post(
    "/{session_id}/csv-imports",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
def create_transaction_csv_import(
    session_id: uuid.UUID,
    body: TransactionCsvImportCreate,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> MessageOut:
    """Persist a CSV import receipt in the selected conversation history.

    The transaction rows themselves were already committed by the transaction
    import endpoint. This records the import as a durable user-side chat event.
    """
    session = _session_for_user(db, current.id, session_id)
    created_at = datetime.now(timezone.utc)
    content = f"Imported {body.imported_count} transaction(s) from {body.filename}."
    if body.skipped_count:
        content += f" Skipped {body.skipped_count} row(s)."
    message = ChatMessage(
        session_id=session.id,
        role="user",
        content=content,
        agent=None,
        metadata_={
            "source": "transaction_csv_import",
            "filename": body.filename,
            "imported_count": str(body.imported_count),
            "skipped_count": str(body.skipped_count),
            "preview": json.dumps(
                [row.model_dump(mode="json") for row in body.preview],
                ensure_ascii=False,
            ),
            "sample_errors": json.dumps(body.sample_errors, ensure_ascii=False),
        },
        created_at=created_at,
    )
    db.add(message)
    db.add(
        MemoryChunk(
            user_id=current.id,
            source="chat",
            content=f"User: {_transaction_csv_import_memory_text(body)}",
            created_at=created_at,
        )
    )
    if session.title == "New chat" or not session.title.strip():
        session.title = f"CSV import: {body.filename}"[:200]
    session.updated_at = created_at
    db.commit()
    db.refresh(message)
    return MessageOut.from_row(message)


@router.post(
    "/{session_id}/invoice-imports",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
def create_invoice_import(
    session_id: uuid.UUID,
    body: InvoiceImportCreate,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> MessageOut:
    """Persist a parsed invoice as a user attachment in conversation history."""
    session = _session_for_user(db, current.id, session_id)
    created_at = datetime.now(timezone.utc)
    message = ChatMessage(
        session_id=session.id,
        role="user",
        content=f"Uploaded invoice: {body.filename}.",
        agent=None,
        metadata_={
            "source": "invoice_import",
            "filename": body.filename,
            "source_type": body.source_type,
            "confidence": body.confidence,
            "warnings": json.dumps(body.warnings, ensure_ascii=False),
            "invoice_payload": body.invoice.model_dump_json(),
        },
        created_at=created_at,
    )
    db.add(message)
    # Include extracted fields in recent conversational context so follow-up
    # questions about this invoice can be grounded without rerunning OCR.
    db.add(
        MemoryChunk(
            user_id=current.id,
            source="chat",
            content=f"User: {_invoice_memory_text(body)}",
            created_at=created_at,
        )
    )
    if session.title == "New chat" or not session.title.strip():
        session.title = f"Invoice: {body.filename}"[:200]
    session.updated_at = created_at
    db.commit()
    db.refresh(message)
    return MessageOut.from_row(message)


@router.get("/{session_id}/messages", response_model=list[MessageOut])
def list_messages(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> list[MessageOut]:
    _session_for_user(db, current.id, session_id)
    rows = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(
            ChatMessage.created_at.asc(),
            # Historical rows may have equal timestamps because PostgreSQL now() is
            # stable for a whole transaction. In that tie, show the user turn first.
            case((ChatMessage.role == "user", 0), else_=1),
            ChatMessage.id.asc(),
        )
    ).all()
    return [MessageOut.from_row(row) for row in rows]
