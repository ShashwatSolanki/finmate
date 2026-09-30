from decimal import Decimal
from uuid import uuid4

import numpy as np
import pytest

from app.invoice.schemas import ParsedLineItem, StructuredInvoice
from app.rag import memory_store
from app.services import email_service


def test_memory_persistence_trims_content_and_rejects_empty(database) -> None:
    user_id = uuid4()
    with database() as db:
        memory_id = memory_store.add_memory(db, user_id, "  remembered income  ", source="onboarding")
        stored = db.get(memory_store.MemoryChunk, memory_id)

    assert stored is not None
    assert stored.content == "remembered income"
    assert stored.source == "onboarding"

    with database() as db, pytest.raises(ValueError, match="empty content"):
        memory_store.add_memory(db, user_id, "   ")


def test_memory_search_ranks_similar_content_and_scopes_user(database, monkeypatch) -> None:
    user_id = uuid4()
    other_user = uuid4()
    with database() as db:
        memory_store.add_memory(db, user_id, "monthly income is 50000", source="chat")
        memory_store.add_memory(db, user_id, "favorite color is blue", source="chat")
        memory_store.add_memory(db, other_user, "monthly income is 99999", source="chat")

    vectors = {
        "monthly income": np.array([1.0, 0.0]),
        "monthly income is 50000": np.array([0.9, 0.1]),
        "favorite color is blue": np.array([0.0, 1.0]),
    }
    monkeypatch.setattr(memory_store, "encode_texts", lambda texts: np.array([vectors[text] for text in texts]))
    with database() as db:
        results = memory_store.search_memory(db, user_id, "monthly income", k=2, min_similarity=0.5)

    assert results == ["monthly income is 50000"]


def test_memory_search_falls_back_when_embedding_fails(database, monkeypatch) -> None:
    user_id = uuid4()
    with database() as db:
        memory_store.add_memory(db, user_id, "first", source="chat")
        memory_store.add_memory(db, user_id, "second", source="chat")

    monkeypatch.setattr(memory_store, "encode_texts", lambda texts: (_ for _ in ()).throw(RuntimeError("model unavailable")))
    with database() as db:
        assert memory_store.search_memory(db, user_id, "anything", k=1) in (["first"], ["second"])


def test_invoice_schema_validates_required_line_item_fields() -> None:
    item = ParsedLineItem(description="Consulting", quantity=2, unit_price=Decimal("100"), amount=Decimal("200"))
    invoice = StructuredInvoice(currency="USD", line_items=[item], total=Decimal("200"))
    assert invoice.line_items[0].amount == Decimal("200")

    with pytest.raises(ValueError):
        ParsedLineItem(description="", amount=Decimal("1"))


def test_email_service_generates_numeric_otps_and_mock_sends(monkeypatch) -> None:
    monkeypatch.setattr(email_service.settings, "email_mock_mode", True)
    otp = email_service.generate_otp(8)
    assert len(otp) == 8
    assert otp.isdecimal()
    assert email_service.send_verification_email("person@example.com", otp) is True
    assert email_service.send_password_reset_email("person@example.com", otp) is True