from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.orchestrator import run_turn
from app.agents.types import AgentName
from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.rag.memory_store import add_memory, search_memory

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    """Force a specialist, or leave null for hybrid auto-routing."""
    agent: AgentName | None = None


class ChatResponse(BaseModel):
    agent: str
    reply: str
    planned_steps: list[str]
    metadata: dict[str, str] = Field(default_factory=dict)


@router.post("/message", response_model=ChatResponse)
def chat_message(
    body: ChatRequest,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> ChatResponse:
    ctx_docs = search_memory(db, current.id, body.message, k=5)
    rag = "\n---\n".join(ctx_docs) if ctx_docs else ""

    result = run_turn(
        current.id,
        body.message,
        db,
        agent=body.agent,
        rag_context=rag or None,
    )

    meta = dict(result.metadata)
    meta["rag_chunks_used"] = str(len(ctx_docs))

    try:
        add_memory(db, current.id, f"User: {body.message[:4000]}", source="chat")
        add_memory(
            db,
            current.id,
            f"Assistant ({result.agent.value}): {result.reply[:3500]}",
            source="chat",
        )
    except Exception:
        # Chat response still returned if memory indexing fails
        meta["memory_persisted"] = "false"
    else:
        meta["memory_persisted"] = "true"

    return ChatResponse(
        agent=result.agent.value,
        reply=result.reply,
        planned_steps=result.planned_steps,
        metadata=meta,
    )
