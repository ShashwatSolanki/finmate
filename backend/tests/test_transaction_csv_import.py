"""CSV transaction import idempotency and exact-match duplicate protection."""

import uuid
from datetime import date
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.deps import get_current_user, get_db
from app.api.routes.transactions import router as transactions_router
from app.db.models import MemoryChunk, Transaction, User


def _client_for(database):
    user = User(
        id=uuid.uuid4(),
        email=f"csv-import-{uuid.uuid4().hex[:8]}@example.test",
        password_hash="test-hash",
        is_verified=True,
    )
    with database.begin() as db:
        db.add(user)

    app = FastAPI()
    app.include_router(transactions_router, prefix="/api/transactions")

    def override_db():
        with database() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app), user


CSV_TEXT = (
    "occurred_on,amount,category,description,currency\n"
    "2026-10-10,-450.00,Food,Cafe lunch,INR\n"
    "2026-10-01,-1200.00,Rent,October rent,INR\n"
)


def test_reuploading_the_exact_csv_is_a_no_op(database):
    client, user = _client_for(database)
    request = {"csv_text": CSV_TEXT}

    with client:
        first = client.post("/api/transactions/import/csv", json=request)
        second = client.post("/api/transactions/import/csv", json=request)

    assert first.status_code == 200
    assert first.json()["imported_count"] == 2
    assert first.json()["skipped_count"] == 0
    assert first.json()["already_imported"] is False

    assert second.status_code == 200
    assert second.json()["imported_count"] == 0
    assert second.json()["already_imported"] is True
    assert any("already imported" in error.lower() for error in second.json()["sample_errors"])

    with database() as db:
        count = db.scalar(select(func.count(Transaction.id)).where(Transaction.user_id == user.id))
        fingerprints = db.scalar(
            select(func.count(MemoryChunk.id)).where(
                MemoryChunk.user_id == user.id,
                MemoryChunk.source == "transaction_csv_import_fingerprint",
            )
        )
    assert count == 2
    assert fingerprints == 1


def test_exact_match_rows_from_legacy_imports_are_skipped(database):
    client, user = _client_for(database)
    with database.begin() as db:
        db.add(
            Transaction(
                user_id=user.id,
                amount=Decimal("-450.00"),
                currency="INR",
                category="Food",
                description="Cafe lunch",
                occurred_on=date(2026, 10, 10),
            )
        )

    with client:
        response = client.post("/api/transactions/import/csv", json={"csv_text": CSV_TEXT})
        repeated = client.post("/api/transactions/import/csv", json={"csv_text": CSV_TEXT})

    assert response.status_code == 200
    payload = response.json()
    assert payload["imported_count"] == 1
    assert payload["skipped_count"] == 1
    assert payload["duplicate_rows_skipped"] == 1
    assert any("exact-match transaction already exists" in error.lower() for error in payload["sample_errors"])

    assert repeated.status_code == 200
    assert repeated.json()["already_imported"] is True
    assert repeated.json()["imported_count"] == 0

    with database() as db:
        rows = db.scalars(
            select(Transaction).where(Transaction.user_id == user.id).order_by(Transaction.occurred_on)
        ).all()
    assert len(rows) == 2
    assert {(row.description, row.amount) for row in rows} == {
        ("Cafe lunch", Decimal("-450.00")),
        ("October rent", Decimal("-1200.00")),
    }
