"""Regression tests for readable chat replies and deterministic history ordering."""

from datetime import datetime, timezone
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.types import AgentName
from app.api.deps import get_current_user, get_db
from app.api.routes.chat import _clean_assistant_display, _enforce_reply_contract
from app.api.routes.conversations import router as conversations_router
from app.db.models import ChatMessage, ChatSession, User


def test_reply_contract_preserves_portfolio_rows_and_paragraphs():
    raw = (
        "[AGENT: INVESTMENT]\n\n"
        "Portfolio Snapshot\n\n"
        "HDFCBANK.NS | 15 units | Current: 709.40 INR\n"
        "INFY.NS | 10 units | Current: 1,020.00 INR\n"
        "TCS.NS | 5 units | Current: 2,125.00 INR\n\n"
        "Total invested: 67,500.00 INR\n"
        "Current value: 42,987.50 INR\n"
        "Unrealized P/L: -24,512.50 INR (-36.31%)\n\n"
        "Note: P/L is unrealized.\n\n"
        '{"intent":"investment_info","steps":["check holdings"],"tools_needed":[],"notes":"test"}'
    )

    normalized = _enforce_reply_contract(raw, AgentName.INVESTMENT_ANALYSER)
    visible = _clean_assistant_display(normalized)

    assert "HDFCBANK.NS | 15 units | Current: 709.40 INR\nINFY.NS | 10 units" in visible
    assert "Portfolio Snapshot\n\nHDFCBANK.NS" in visible
    assert "Current value: 42,987.50 INR\nUnrealized P/L: -24,512.50 INR" in visible
    assert "[AGENT:" not in visible
    assert '"intent"' not in visible


def test_conversation_history_puts_user_turn_before_reply_when_timestamps_tie(database):
    user = User(
        id=uuid.uuid4(),
        email="chat-order@example.test",
        password_hash="test-hash",
        is_verified=True,
    )
    session = ChatSession(user_id=user.id, title="Ordering test")
    same_timestamp = datetime(2026, 10, 12, 10, 0, 0, tzinfo=timezone.utc)

    with database.begin() as db:
        db.add(user)
        db.add(session)
        db.flush()
        # Insert the assistant first to prove ordering doesn't depend on insertion order or UUID.
        db.add_all(
            [
                ChatMessage(
                    session_id=session.id,
                    role="assistant",
                    content="Here is the answer.",
                    created_at=same_timestamp,
                ),
                ChatMessage(
                    session_id=session.id,
                    role="user",
                    content="How is my portfolio looking?",
                    created_at=same_timestamp,
                ),
            ]
        )
        db.flush()
        session_id = session.id

    app = FastAPI()
    app.include_router(conversations_router, prefix="/api/conversations")

    def override_db():
        with database() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user

    with TestClient(app) as client:
        response = client.get(f"/api/conversations/{session_id}/messages")

    assert response.status_code == 200
    messages = response.json()
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "How is my portfolio looking?"
