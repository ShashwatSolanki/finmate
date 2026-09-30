import uuid
from collections.abc import Generator
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_current_user, get_db
from app.api.routes.budgets import router as budgets_router
from app.api.routes.portfolio import router as portfolio_router
from app.api.routes.transactions import router as transactions_router
from app.db.models import InvestmentHolding, Transaction, User


@pytest.fixture
def financial_api_client(database: sessionmaker[Session]):
    app = FastAPI()
    app.include_router(transactions_router, prefix="/api/transactions")
    app.include_router(portfolio_router, prefix="/api/portfolio")
    app.include_router(budgets_router, prefix="/api/budgets")
    user = User(id=uuid.uuid4(), email="api-owner@example.com", password_hash="hashed")

    with database.begin() as db:
        db.add(user)

    def override_db() -> Generator[Session, None, None]:
        with database() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        yield client, user, database


def budget_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "category": "Food",
        "limit_amount": "250.00",
        "period_start": "2026-01-01",
        "period_end": "2026-01-31",
    }
    payload.update(overrides)
    return payload


def test_transaction_api_crud_summary_and_csv_flow(financial_api_client) -> None:
    client, user, database = financial_api_client

    created = client.post(
        "/api/transactions",
        json={
            "amount": "-50.00",
            "currency": "USD",
            "category": "Food",
            "description": "Lunch",
            "occurred_on": "2026-01-15",
        },
    )
    assert created.status_code == 200
    transaction = created.json()
    assert transaction["user_id"] == str(user.id)
    assert transaction["amount"] == "-50.00"

    listed = client.get("/api/transactions")
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    summary = client.get("/api/transactions/summary/monthly?year=2026&month=1")
    assert summary.status_code == 200
    assert summary.json() == {"year": 2026, "month": 1, "total_expenses": "-50.00"}

    imported = client.post(
        "/api/transactions/import/csv",
        json={
            "csv_text": "occurred_on,amount,category\n2026-01-20,25.50,Transport\n",
        },
    )
    assert imported.status_code == 200
    assert imported.json()["imported_count"] == 1

    exported = client.get("/api/transactions/export/csv")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    assert "Lunch" in exported.text
    assert "Transport" in exported.text

    with database() as db:
        assert db.query(Transaction).count() == 2


def test_transaction_api_validation_and_authentication(financial_api_client) -> None:
    client, _, _ = financial_api_client

    invalid = client.post(
        "/api/transactions",
        json={"amount": "not-number", "occurred_on": "not-a-date"},
    )
    assert invalid.status_code == 422
    assert client.get("/api/transactions/summary/monthly?year=2026&month=13").status_code == 422

    client.app.dependency_overrides.pop(get_current_user)
    assert client.get("/api/transactions").status_code == 401


def test_portfolio_api_persists_holdings_and_returns_valuation(financial_api_client, monkeypatch) -> None:
    client, _, database = financial_api_client
    monkeypatch.setattr("app.api.routes.portfolio.get_ticker", lambda symbol: SimpleNamespace(info={"currentPrice": 125}))

    created = client.post(
        "/api/portfolio/holdings",
        json={"symbol": "aapl", "quantity": "2", "average_cost": "100", "currency": "usd"},
    )
    assert created.status_code == 201
    assert created.json()["symbol"] == "AAPL"
    assert created.json()["currency"] == "USD"

    listed = client.get("/api/portfolio/holdings")
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    summary = client.get("/api/portfolio/summary")
    assert summary.status_code == 200
    assert Decimal(summary.json()[0]["market_value"]) == Decimal("250.00")
    assert Decimal(summary.json()[0]["unrealized_profit"]) == Decimal("50.00")

    deleted = client.delete("/api/portfolio/holdings/AAPL")
    assert deleted.status_code == 204
    assert client.get("/api/portfolio/holdings").json() == []

    with database() as db:
        assert db.query(InvestmentHolding).count() == 0


def test_portfolio_api_rejects_invalid_holding_and_missing_resource(financial_api_client) -> None:
    client, _, _ = financial_api_client

    invalid = client.post(
        "/api/portfolio/holdings",
        json={"symbol": "AAPL", "quantity": "0", "average_cost": "100"},
    )
    assert invalid.status_code == 422
    assert client.delete("/api/portfolio/holdings/UNKNOWN").status_code == 404


def test_budget_api_crud_and_user_isolation(financial_api_client) -> None:
    client, user, database = financial_api_client

    created = client.post("/api/budgets", json=budget_payload())
    assert created.status_code == 201
    budget_id = created.json()["id"]
    assert created.json()["user_id"] == str(user.id)

    assert client.get("/api/budgets").status_code == 200
    updated = client.patch(f"/api/budgets/{budget_id}", json={"limit_amount": "300.00"})
    assert updated.status_code == 200
    assert updated.json()["limit_amount"] == "300.00"

    other_user = User(id=uuid.uuid4(), email="other-api@example.com", password_hash="hashed")
    with database.begin() as db:
        db.add(other_user)
    client.app.dependency_overrides[get_current_user] = lambda: other_user
    assert client.get(f"/api/budgets/{budget_id}").status_code == 404
    assert client.patch(f"/api/budgets/{budget_id}", json={"category": "Other"}).status_code == 404
    assert client.delete(f"/api/budgets/{budget_id}").status_code == 404
