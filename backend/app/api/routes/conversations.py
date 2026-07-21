import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import ChatMessage, ChatSession, User
from app.db.session import get_db

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
        .order_by(ChatMessage.created_at.asc())
    ).all()
    return [MessageOut.from_row(row) for row in rows]
