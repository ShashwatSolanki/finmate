import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.agents.agentic_orchestrator import build_plan
from app.agents.orchestrator import run_turn
from app.agents.types import AgentName
from app.api.deps import get_current_user
from app.db.models import ChatMessage, ChatSession, MemoryChunk, User
from app.db.session import get_db
from app.config import settings
from app.rag.memory_store import add_memory, search_memory

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    """Force a specialist, or leave null for hybrid auto-routing."""
    agent: AgentName | None = None
    session_id: uuid.UUID | None = None


class ChatResponse(BaseModel):
    agent: str
    reply: str
    planned_steps: list[str]
    metadata: dict[str, str] = Field(default_factory=dict)
    session_id: uuid.UUID | None = None


def _normalized_tag(agent: AgentName) -> str:
    if agent == AgentName.INVOICE_GENERATOR:
        return "[AGENT: INVOICE]"
    if agent == AgentName.INVESTMENT_ANALYSER:
        return "[AGENT: INVESTMENT]"
    return "[AGENT: BUDGET]"


def _contains_raw_agent_trace(text: str) -> bool:
    """Detect tool-router/debug fragments that must never be shown as an answer."""
    return bool(
        re.search(
            r"\[\s*tokenized_tools\s*\]|\btools_needed\s*=|\btool_name\s*=|"
            r"\bAgent says:|\bAdditional tools:|\bRisk discussion:|"
            r"\bRAG chunks used\b|\bconfidence factors\b|"
            r"\bexecution path\s*(?:rules|specialist|agentic)\b",
            text or "",
            re.I,
        )
    )


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
    """Keep machine-readable markers out of the prose without flattening its layout."""
    raw = (reply or "").replace("\r", "").strip()
    tag = _normalized_tag(agent)
    if not raw:
        return f"{tag}\n\nI can help with your request.\n\n{_canonical_json_tail(agent)}"

    # Preserve headings, portfolio rows, bullets, and paragraph breaks. Joining
    # every non-empty line with spaces turned structured financial answers into
    # one unreadable paragraph.
    if _contains_raw_agent_trace(raw):
        # Fail closed at the API boundary even if a specialist or synthesis step
        # bypasses its own output validator. Never leak tool/debug traces as advice.
        return (
            f"{tag}\n\n"
            "I couldn't produce a readable, verified answer from the agent output. "
            "No financial conclusion should be drawn from this response. Please retry the request "
            "or select the relevant specialist directly.\n\n"
            f"{_canonical_json_tail(agent)}"
        )

    paragraphs: list[str] = []
    current_lines: list[str] = []
    json_line = ""

    def flush_paragraph() -> None:
        if current_lines:
            paragraphs.append("\n".join(current_lines))
            current_lines.clear()

    for line in raw.split("\n"):
        text = line.strip()
        if not text:
            flush_paragraph()
            continue
        if text.startswith("[AGENT:") and text.endswith("]"):
            flush_paragraph()
            continue
        if text.startswith("{") and text.endswith("}"):
            try:
                json.loads(text)
            except json.JSONDecodeError:
                current_lines.append(text)
            else:
                flush_paragraph()
                json_line = text
            continue
        current_lines.append(text)

    flush_paragraph()
    prose = "\n\n".join(paragraphs).strip() or "I can help with your request."
    if not json_line:
        json_line = _canonical_json_tail(agent)
    return f"{tag}\n\n{prose}\n\n{json_line}"


def _build_recent_context(db: Session, user_id, turns: int = 3) -> str | None:
    rows = db.scalars(
        select(MemoryChunk)
        .where(MemoryChunk.user_id == user_id, MemoryChunk.source == "chat")
        .order_by(
            MemoryChunk.created_at.desc(),
            # Reverse tie order so the later assistant message follows its user turn
            # after this newest-first result is reversed for the prompt.
            case((MemoryChunk.content.like("Assistant (%"), 0), else_=1),
            MemoryChunk.id.desc(),
        )
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
    rows = db.scalars(
        select(MemoryChunk)
        .where(MemoryChunk.user_id == user_id, MemoryChunk.source == "chat")
        .order_by(
            MemoryChunk.created_at.desc(),
            case((MemoryChunk.content.like("Assistant (%"), 0), else_=1),
            MemoryChunk.id.desc(),
        )
        .limit(12)
    ).all()
    for row in rows:
        text = row.content or ""
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

    # Explicit multi-domain requests must reach the agentic planner instead of
    # being hijacked by conversational follow-up routing.
    if build_plan(message) is not None:
        return None

    # Do not let an earlier conversation route a new, high-signal finance
    # request to the previous specialist. Explicit requests should be freshly
    # classified; the override is reserved for short/ambiguous follow-ups.
    if _is_high_signal_user_message(message):
        return None

    t = message.strip().lower()
    if not t:
        return None

    if last_agent == AgentName.INVESTMENT_ANALYSER:
        investish = bool(
            re.search(
                r"\b(invest|investment|stock|stocks|shares|portfolio|risk|horizon|"
                r"apple|google|microsoft|aapl|msft|googl|nvda|tsla|ticker|market|sip|allocate)\b",
                t,
            )
        ) or bool(re.search(r"\$[A-Za-z]{1,5}\b", message))
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


def _title_from_message(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip())
    if not cleaned:
        return "New chat"
    return cleaned[:60] + ("…" if len(cleaned) > 60 else "")


def _get_or_create_session(db: Session, user_id: uuid.UUID, session_id: uuid.UUID | None, message: str) -> ChatSession:
    if session_id is not None:
        row = db.scalar(
            select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user_id)
        )
        if row:
            return row
    row = ChatSession(user_id=user_id, title=_title_from_message(message))
    db.add(row)
    db.flush()
    return row


def _clean_assistant_display(reply: str) -> str:
    """Remove protocol metadata while retaining readable paragraph/list structure."""
    paragraphs: list[str] = []
    current_lines: list[str] = []

    def flush_paragraph() -> None:
        if current_lines:
            paragraphs.append("\n".join(current_lines))
            current_lines.clear()

    for line in reply.replace("\r", "").split("\n"):
        text = line.strip()
        if not text:
            flush_paragraph()
            continue
        if text.startswith("[AGENT:") and text.endswith("]"):
            flush_paragraph()
            continue
        if text.startswith("{") and text.endswith("}"):
            try:
                json.loads(text)
            except json.JSONDecodeError:
                current_lines.append(text)
            else:
                flush_paragraph()
            continue
        current_lines.append(text)

    flush_paragraph()
    return "\n\n".join(paragraphs).strip() or reply.strip()


@router.post("/message", response_model=ChatResponse)
def chat_message(
    body: ChatRequest,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> ChatResponse:
    ctx_docs = (
        search_memory(db, current.id, body.message, k=5, min_similarity=0.22)
        if settings.finmate_use_rag
        else []
    )
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

    meta = {str(key): str(value) for key, value in result.metadata.items()}

    # Preserve structured invoice export artifacts explicitly at the API
    # boundary. These are consumed by the UI for PDF/CSV export.
    if result.agent == AgentName.INVOICE_GENERATOR:
        for key in ("invoice_ref", "invoice_payload", "invoice_actions"):
            value = result.metadata.get(key)
            if value is not None and str(value).strip():
                meta[key] = str(value)

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

    session = _get_or_create_session(db, current.id, body.session_id, body.message)
    # Explicit timestamps prevent PostgreSQL's transaction-stable now() default
    # from giving the user message and its reply identical timestamps.
    turn_time = datetime.now(timezone.utc)
    db.add(
        ChatMessage(
            session_id=session.id,
            role="user",
            content=body.message,
            created_at=turn_time,
        )
    )
    display_reply = _clean_assistant_display(result.reply)
    db.add(
        ChatMessage(
            session_id=session.id,
            role="assistant",
            content=display_reply,
            agent=result.agent.value,
            metadata_=meta,
            created_at=turn_time + timedelta(microseconds=1),
        )
    )
    if session.title == "New chat" or not session.title.strip():
        session.title = _title_from_message(body.message)
    session.updated_at = datetime.now(timezone.utc)
    db.commit()

    return ChatResponse(
        agent=result.agent.value,
        reply=result.reply,
        planned_steps=result.planned_steps,
        metadata=dict(meta),
        session_id=session.id,
    )
