import uuid
from collections.abc import Generator
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.agents import budget_planner
from app.agents.finance_context import extract_monthly_income
from app.agents.types import AgentName
from app.api.deps import get_current_user, get_db
from app.api.routes.budgets import router as budgets_router
from app.api.routes.portfolio import router as portfolio_router
from app.api.routes.transactions import router as transactions_router
from app.db.models import Budget, InvestmentHolding, Transaction, User
from app.services.spending_insights import category_delta_vs_prior_month


@pytest.fixture
def integration_client(database: sessionmaker[Session]):
    app = FastAPI()
    app.include_router(transactions_router, prefix="/api/transactions")
    app.include_router(portfolio_router, prefix="/api/portfolio")
    app.include_router(budgets_router, prefix="/api/budgets")
    user = User(id=uuid.uuid4(), email="integration@example.com", password_hash="hashed")

    with database.begin() as db:
        db.add(user)

    def override_db() -> Generator[Session, None, None]:
        with database() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        yield client, user, database


def test_transaction_persistence_to_budget_spending_flow(integration_client, monkeypatch) -> None:
    client, user, database = integration_client
    occurred_on = date.today() - timedelta(days=3)

    created = client.post(
        "/api/transactions",
        json={
            "amount": "-125.00",
            "currency": "USD",
            "category": "Food",
            "description": "Groceries",
            "occurred_on": occurred_on.isoformat(),
        },
    )
    assert created.status_code == 200

    budget = client.post(
        "/api/budgets",
        json={
            "category": "Food",
            "limit_amount": "200.00",
            "period_start": (occurred_on - timedelta(days=10)).isoformat(),
            "period_end": (occurred_on + timedelta(days=10)).isoformat(),
        },
    )
    assert budget.status_code == 201

    with database() as db:
        stored_transaction = db.query(Transaction).filter_by(user_id=user.id).one()
        stored_budget = db.query(Budget).filter_by(user_id=user.id).one()
        spending = abs(stored_transaction.amount)
        remaining = stored_budget.limit_amount - spending
        monkeypatch.setattr(budget_planner.settings, "finmate_use_llm", False)
        result = budget_planner.run(user.id, "Help me manage my food budget", db)

    assert stored_transaction.description == "Groceries"
    assert spending == Decimal("125.00")
    assert remaining == Decimal("75.00")
    assert "Food" in result.reply
    assert result.metadata["categories_found"] == "1"


def test_csv_import_persistence_to_aggregation_flow(integration_client) -> None:
    client, user, database = integration_client
    csv_text = (
        "occurred_on,amount,category,description\n"
        "2026-02-01,-80.00,Transport,Metro\n"
        "2026-02-12,-120.00,Transport,Cab\n"
    )

    imported = client.post("/api/transactions/import/csv", json={"csv_text": csv_text})
    assert imported.status_code == 200
    assert imported.json()["imported_count"] == 2

    with database() as db:
        rows = db.query(Transaction).filter_by(user_id=user.id).all()
        month_over_month = category_delta_vs_prior_month(db, user.id, ref=date(2026, 3, 1))

    assert len(rows) == 2
    assert sum((row.amount for row in rows), Decimal("0")) == Decimal("-200.00")
    assert "Transport: up from 0 to -200.00" in month_over_month


def test_investment_creation_to_mocked_valuation_flow(integration_client, monkeypatch) -> None:
    client, user, database = integration_client
    monkeypatch.setattr(
        "app.api.routes.portfolio.get_ticker",
        lambda symbol: SimpleNamespace(info={"currentPrice": 125}),
    )

    created = client.post(
        "/api/portfolio/holdings",
        json={"symbol": "AAPL", "quantity": "4", "average_cost": "100", "currency": "USD"},
    )
    assert created.status_code == 201

    valuation = client.get("/api/portfolio/summary")
    assert valuation.status_code == 200
    holding = valuation.json()[0]
    assert Decimal(holding["cost_basis"]) == Decimal("400")
    assert Decimal(holding["market_value"]) == Decimal("500")
    assert Decimal(holding["unrealized_profit"]) == Decimal("100")
    assert Decimal(holding["unrealized_profit_pct"]) == Decimal("25")

    with database() as db:
        stored = db.query(InvestmentHolding).filter_by(user_id=user.id, symbol="AAPL").one()
    assert stored.quantity == Decimal("4.00000000")


def test_financial_context_reaches_budget_agent(database, monkeypatch) -> None:
    user_id = uuid.uuid4()
    with database.begin() as db:
        db.add(Transaction(
            user_id=user_id,
            amount=Decimal("-75.00"),
            currency="INR",
            category="Dining",
            description="Dinner",
            occurred_on=date.today(),
        ))

    income, currency = extract_monthly_income("My monthly income is INR 50,000")
    assert income == Decimal("50000")
    assert currency == "INR"

    monkeypatch.setattr(budget_planner.settings, "finmate_use_llm", False)
    with database() as db:
        result = budget_planner.run(
            user_id,
            "Create a plan from my monthly income of INR 50,000",
            db,
            rag_context="risk tolerance: moderate",
        )

    assert result.agent == AgentName.BUDGET_PLANNER
    assert result.metadata["income_detected"] == "50,000.00 INR"
    assert "Dining" in result.reply