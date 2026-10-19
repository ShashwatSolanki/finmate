"""Regression tests for readable chat replies and deterministic history ordering."""

from datetime import datetime, timezone
import json
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.types import AgentName
from app.api.deps import get_current_user, get_db
from app.api.routes.chat import _build_recent_context, _clean_assistant_display, _enforce_reply_contract
from app.api.routes.conversations import router as conversations_router
from app.db.models import ChatMessage, ChatSession, MemoryChunk, User


def test_reply_contract_rejects_raw_agent_tool_traces():
    raw = (
        "[AGENT: BUDGET]\n\n"
        "Budget Planner:\n"
        "[\n"
        '"Agent says: Risk assessment needs date and asset valuation first.",\n'
        '"Additional tools: date_set, tool_name=budget_recommendation",\n'
        "tools_needed=[date_set, tool_name=budget_recommendation]"
        "\n]"
    )

    visible = _clean_assistant_display(
        _enforce_reply_contract(raw, AgentName.BUDGET_PLANNER)
    )

    assert "couldn't produce a readable, verified answer" in visible.lower()
    assert "Agent says:" not in visible
    assert "tools_needed=" not in visible
    assert "tool_name=" not in visible
    assert "Additional tools:" not in visible


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


def test_recent_memory_context_orders_tied_user_and_assistant_snippets(database):
    user = User(
        id=uuid.uuid4(),
        email="memory-order@example.test",
        password_hash="test-hash",
        is_verified=True,
    )
    same_timestamp = datetime(2026, 10, 12, 10, 0, 0, tzinfo=timezone.utc)

    with database.begin() as db:
        db.add(user)
        db.flush()
        db.add_all(
            [
                MemoryChunk(
                    user_id=user.id,
                    source="chat",
                    content="Assistant (investment_analyser): latest answer",
                    created_at=same_timestamp,
                ),
                MemoryChunk(
                    user_id=user.id,
                    source="chat",
                    content="User: latest question",
                    created_at=same_timestamp,
                ),
            ]
        )

    with database() as db:
        context = _build_recent_context(db, user.id, turns=1)

    assert context == "User: latest question\nAssistant (investment_analyser): latest answer"


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



def test_invoice_import_is_persisted_as_user_attachment_and_recent_context(database):
    user = User(
        id=uuid.uuid4(),
        email="invoice-attachment@example.test",
        password_hash="test-hash",
        is_verified=True,
    )
    session = ChatSession(user_id=user.id, title="New chat")

    with database.begin() as db:
        db.add(user)
        db.add(session)
        db.flush()
        session_id = session.id

    app = FastAPI()
    app.include_router(conversations_router, prefix="/api/conversations")

    def override_db():
        with database() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    request_body = {
        "filename": "cafe-bill.png",
        "source_type": "image",
        "confidence": "medium",
        "warnings": ["Line-item amounts do not match the printed subtotal; review OCR."],
        "invoice": {
            "invoice_number": "INV-204",
            "vendor_name": "Cafe Aurora",
            "currency": "INR",
            "line_items": [
                {
                    "description": "Coffee and sandwich",
                    "quantity": 1,
                    "unit_price": "450.00",
                    "amount": "450.00",
                }
            ],
            "subtotal": "450.00",
            "tax": "0.00",
            "total": "450.00",
        },
    }

    with TestClient(app) as client:
        response = client.post(
            f"/api/conversations/{session_id}/invoice-imports",
            json=request_body,
        )
        history_response = client.get(f"/api/conversations/{session_id}/messages")

    assert response.status_code == 201
    saved = response.json()
    assert saved["role"] == "user"
    assert saved["content"] == "Uploaded invoice: cafe-bill.png."
    assert saved["metadata"]["source"] == "invoice_import"
    assert saved["metadata"]["filename"] == "cafe-bill.png"
    assert json.loads(saved["metadata"]["warnings"]) == [
        "Line-item amounts do not match the printed subtotal; review OCR."
    ]
    parsed_invoice = json.loads(saved["metadata"]["invoice_payload"])
    assert parsed_invoice["vendor_name"] == "Cafe Aurora"
    assert parsed_invoice["total"] == "450.00"

    assert history_response.status_code == 200
    history = history_response.json()
    assert len(history) == 1
    assert history[0]["metadata"]["source"] == "invoice_import"

    with database() as db:
        recent_context = _build_recent_context(db, user.id, turns=1)
    assert recent_context is not None
    assert "Cafe Aurora" in recent_context
    assert "Coffee and sandwich: 450.00 INR" in recent_context
    assert "Extraction warnings:" in recent_context
    assert "review OCR" in recent_context


def test_transaction_csv_import_is_persisted_as_user_attachment(database):
    user = User(
        id=uuid.uuid4(),
        email="csv-attachment@example.test",
        password_hash="test-hash",
        is_verified=True,
    )
    session = ChatSession(user_id=user.id, title="New chat")

    with database.begin() as db:
        db.add(user)
        db.add(session)
        db.flush()
        session_id = session.id

    app = FastAPI()
    app.include_router(conversations_router, prefix="/api/conversations")

    def override_db():
        with database() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    request_body = {
        "filename": "finmate_test_transactions.csv",
        "imported_count": 15,
        "skipped_count": 1,
        "duplicate_rows_skipped": 1,
        "already_imported": False,
        "preview": [
            {
                "amount": "-450.00",
                "currency": "INR",
                "category": "Food",
                "description": "Cafe lunch",
                "occurred_on": "2026-10-10",
            },
            {
                "amount": "-1200.00",
                "currency": "INR",
                "category": "Rent",
                "description": "October rent",
                "occurred_on": "2026-10-01",
            },
        ],
        "sample_errors": ["line 17: unsupported date 'not-a-date'"],
    }

    with TestClient(app) as client:
        response = client.post(
            f"/api/conversations/{session_id}/csv-imports",
            json=request_body,
        )
        history_response = client.get(f"/api/conversations/{session_id}/messages")
        conversations_response = client.get("/api/conversations")

    assert response.status_code == 201
    saved = response.json()
    assert saved["role"] == "user"
    assert saved["content"] == (
        "Imported 15 transaction(s) from finmate_test_transactions.csv. Skipped 1 row(s)."
    )
    assert saved["metadata"]["source"] == "transaction_csv_import"
    assert saved["metadata"]["filename"] == "finmate_test_transactions.csv"
    assert saved["metadata"]["imported_count"] == "15"
    assert saved["metadata"]["skipped_count"] == "1"
    assert saved["metadata"]["duplicate_rows_skipped"] == "1"
    assert saved["metadata"]["already_imported"] == "false"
    assert json.loads(saved["metadata"]["preview"])[0]["description"] == "Cafe lunch"
    assert json.loads(saved["metadata"]["sample_errors"]) == [
        "line 17: unsupported date 'not-a-date'"
    ]

    assert history_response.status_code == 200
    history = history_response.json()
    assert len(history) == 1
    assert history[0]["id"] == saved["id"]
    assert history[0]["metadata"]["source"] == "transaction_csv_import"

    assert conversations_response.status_code == 200
    listed = conversations_response.json()
    assert listed[0]["id"] == str(session_id)
    assert listed[0]["title"] == "CSV import: finmate_test_transactions.csv"
    assert listed[0]["message_count"] == 1

    with database() as db:
        recent_context = _build_recent_context(db, user.id, turns=1)
    assert recent_context is not None
    assert "Imported transactions CSV file: finmate_test_transactions.csv" in recent_context
    assert "exact-match duplicate rows skipped: 1" in recent_context
    assert "Cafe lunch -450.00 INR" in recent_context
    assert "line 17: unsupported date" in recent_context


def test_conversation_title_can_be_customized_and_survives_reload(database):
    user = User(
        id=uuid.uuid4(),
        email="rename-chat@example.test",
        password_hash="test-hash",
        is_verified=True,
    )
    session = ChatSession(user_id=user.id, title="CSV import: transactions.csv")

    with database.begin() as db:
        db.add(user)
        db.add(session)
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
        response = client.patch(
            f"/api/conversations/{session_id}",
            json={"title": "October spending review"},
        )
        list_response = client.get("/api/conversations")

    assert response.status_code == 200
    assert response.json()["title"] == "October spending review"
    assert list_response.status_code == 200
    assert list_response.json()[0]["title"] == "October spending review"
