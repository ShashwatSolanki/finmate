from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
import re

from app.agents.orchestrator import run_turn
from app.agents.types import AgentName
from app.api.deps import get_current_user
from app.db.models import MemoryChunk, User
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


def _normalized_tag(agent: AgentName) -> str:
    if agent == AgentName.INVOICE_GENERATOR:
        return "[AGENT: INVOICE]"
    if agent == AgentName.INVESTMENT_ANALYSER:
        return "[AGENT: INVESTMENT]"
    return "[AGENT: BUDGET]"


def _canonical_json_tail(agent: AgentName) -> str:
    if agent == AgentName.INVOICE_GENERATOR:
        return (
            '{"intent":"create_invoice","steps":["Collect line items","Confirm totals","Generate invoice PDF"],'
            '"tools_needed":["render_invoice_pdf"],"notes":"format fallback"}'
        )
    if agent == AgentName.INVESTMENT_ANALYSER:
        return (
            '{"intent":"investment_info","steps":["Check ticker trend","Align to risk tolerance","Decide allocation"],'
            '"tools_needed":["yfinance_lookup"],"notes":"format fallback"}'
        )
    return (
        '{"intent":"budget_plan","steps":["Review expenses","Set category caps","Automate savings"],'
        '"tools_needed":["list_transactions","set_budget"],"notes":"format fallback"}'
    )


def _enforce_reply_contract(reply: str, agent: AgentName) -> str:
    raw = (reply or "").replace("\r", "").strip()
    tag = _normalized_tag(agent)
    lines = [ln.rstrip() for ln in raw.split("\n") if ln.strip()]
    if not lines:
        return f"{tag}\n\nI can help with your request.\n\n{_canonical_json_tail(agent)}"

    # Keep prose from non-tag / non-json lines only.
    prose_lines: list[str] = []
    json_line = ""
    for ln in lines:
        t = ln.strip()
        if t.startswith("[AGENT:") and t.endswith("]"):
            continue
        if t.startswith("{") and t.endswith("}"):
            json_line = t
            continue
        prose_lines.append(t)

    prose = " ".join(prose_lines).strip() or "I can help with your request."
    if not json_line:
        json_line = _canonical_json_tail(agent)
    return f"{tag}\n\n{prose}\n\n{json_line}"


def _build_recent_context(db: Session, user_id, turns: int = 3) -> str | None:
    rows = db.scalars(
        select(MemoryChunk)
        .where(MemoryChunk.user_id == user_id, MemoryChunk.source == "chat")
        .order_by(MemoryChunk.created_at.desc())
        .limit(max(6, turns * 4))
    ).all()
    if not rows:
        return None
    ordered = list(reversed(rows))
    recent = [r.content.strip() for r in ordered if r.content.strip()][-(turns * 2) :]
    if not recent:
        return None
    return "\n".join(recent)[:3000]


def _latest_onboarding_context(db: Session, user_id) -> str | None:
    row = db.scalar(
        select(MemoryChunk)
        .where(MemoryChunk.user_id == user_id, MemoryChunk.source == "onboarding")
        .order_by(MemoryChunk.created_at.desc())
        .limit(1)
    )
    if not row or not row.content.strip():
        return None
    return row.content.strip()[:1200]


def _is_high_signal_user_message(text: str) -> bool:
    t = text.lower()
    has_digit = any(ch.isdigit() for ch in text)
    finance_terms = (
        "income",
        "salary",
        "rent",
        "expense",
        "spend",
        "budget",
        "save",
        "savings",
        "debt",
        "loan",
        "emi",
        "stock",
        "invest",
        "sip",
        "invoice",
        "tax",
        "category",
    )
    return has_digit or any(term in t for term in finance_terms)


def _latest_assistant_agent(db: Session, user_id) -> AgentName | None:
    row = db.scalar(
        select(MemoryChunk)
        .where(MemoryChunk.user_id == user_id, MemoryChunk.source == "chat")
        .order_by(MemoryChunk.created_at.desc())
        .limit(12)
    )
    if not row or not row.content:
        return None
    text = row.content
    if "Assistant (investment_analyser):" in text:
        return AgentName.INVESTMENT_ANALYSER
    if "Assistant (invoice_generator):" in text:
        return AgentName.INVOICE_GENERATOR
    if "Assistant (budget_planner):" in text:
        return AgentName.BUDGET_PLANNER
    return None


def _followup_agent_override(db: Session, user_id, message: str) -> AgentName | None:
    """
    Keep conversations coherent across short follow-up turns.
    If user was in investment flow, do not bounce to budget for ambiguous follow-ups.
    """
    last_agent = _latest_assistant_agent(db, user_id)
    if last_agent is None:
        return None

    t = message.strip().lower()
    if not t:
        return None

    if last_agent == AgentName.INVESTMENT_ANALYSER:
        investish = bool(
            re.search(
                r"\b(invest|investment|stock|shares|portfolio|risk|horizon|apple|google|microsoft|aapl|msft|googl|how do i|where should i|ration)\b",
                t,
            )
        )
        invoiceish = bool(re.search(r"\b(invoice|bill|receipt|line items|pdf)\b", t))
        if investish and not invoiceish:
            return AgentName.INVESTMENT_ANALYSER
    return None


def _should_store_assistant_reply(reply: str) -> bool:
    s = reply.strip().lower()
    if '"notes":"crisis mode"' in s or "crisis mode" in s:
        return False
    generic_markers = (
        "please share more details",
        "i need more information",
        "clarify your risk tolerance",
        "cannot provide",
    )
    return not any(m in s for m in generic_markers)


@router.post("/message", response_model=ChatResponse)
def chat_message(
    body: ChatRequest,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> ChatResponse:
    ctx_docs = search_memory(db, current.id, body.message, k=5, min_similarity=0.22)
    recent_context = _build_recent_context(db, current.id, turns=3)
    onboarding_context = _latest_onboarding_context(db, current.id)
    rag = "\n---\n".join(ctx_docs) if ctx_docs else ""
    merged_context = "\n\n[Recent conversation]\n" + recent_context if recent_context else ""
    if onboarding_context:
        merged_context = (merged_context + "\n\n[User onboarding profile]\n" + onboarding_context).strip()
    if rag:
        merged_context = (merged_context + "\n\n[Retrieved memory]\n" + rag).strip()

    chosen_agent = body.agent
    if chosen_agent is None:
        chosen_agent = _followup_agent_override(db, current.id, body.message)

    result = run_turn(
        current.id,
        body.message,
        db,
        agent=chosen_agent,
        rag_context=merged_context or None,
    )
    result.reply = _enforce_reply_contract(result.reply, result.agent)

    meta = dict(result.metadata)
    meta["rag_chunks_used"] = str(len(ctx_docs))
    meta["recent_turns_injected"] = "3" if recent_context else "0"
    meta["onboarding_injected"] = "true" if onboarding_context else "false"
    if chosen_agent is not None and body.agent is None:
        meta["followup_agent_override"] = chosen_agent.value

    try:
        if _is_high_signal_user_message(body.message):
            add_memory(db, current.id, f"User: {body.message[:4000]}", source="chat")
            meta["user_memory_stored"] = "true"
        else:
            meta["user_memory_stored"] = "false"

        if _should_store_assistant_reply(result.reply):
            add_memory(
                db,
                current.id,
                f"Assistant ({result.agent.value}): {result.reply[:3500]}",
                source="chat",
            )
            meta["assistant_memory_stored"] = "true"
        else:
            meta["assistant_memory_stored"] = "false"
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
