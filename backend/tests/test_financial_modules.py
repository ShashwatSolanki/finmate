from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pandas as pd

from app.agents.investment_analyser import _portfolio_history_reply
from app.api.routes.transactions import _parse_csv_amount, _parse_csv_date
from app.db.models import Budget, InvestmentHolding, Transaction
from app.services import market_data
from app.services.spending_insights import category_delta_vs_prior_month


def test_transaction_amount_and_date_parsing_handles_common_formats() -> None:
    assert _parse_csv_amount("(1,250.50)") == Decimal("-1250.50")
    assert _parse_csv_amount("Rs. 799.00") == Decimal("799.00")
    assert _parse_csv_date("31/01/2026") == date(2026, 1, 31)
    assert _parse_csv_date("Jan 31, 2026") == date(2026, 1, 31)


def test_transaction_parsing_rejects_malformed_values() -> None:
    import pytest

    with pytest.raises(ValueError, match="invalid amount"):
        _parse_csv_amount("not-money")
    with pytest.raises(ValueError, match="unsupported date"):
        _parse_csv_date("2026.01.31")


def test_monthly_category_aggregation_and_month_over_month_changes(database) -> None:
    user_id = uuid4()
    with database.begin() as db:
        db.add_all(
            [
                Transaction(user_id=user_id, amount=Decimal("100"), category="Food", occurred_on=date(2026, 1, 10)),
                Transaction(user_id=user_id, amount=Decimal("150"), category="Food", occurred_on=date(2026, 2, 10)),
                Transaction(user_id=user_id, amount=Decimal("50"), category="Travel", occurred_on=date(2026, 2, 11)),
                Transaction(user_id=uuid4(), amount=Decimal("999"), category="Food", occurred_on=date(2026, 2, 10)),
            ]
        )

    with database() as db:
        message = category_delta_vs_prior_month(db, user_id, ref=date(2026, 3, 1))

    assert "Food: up about 50%" in message
    assert "Travel: up from 0 to 50" in message
    assert "999" not in message


def test_budget_spending_total_and_remaining_amount(database) -> None:
    user_id = uuid4()
    with database.begin() as db:
        budget = Budget(
            user_id=user_id,
            category="Food",
            limit_amount=Decimal("300"),
            period_start=date(2026, 2, 1),
            period_end=date(2026, 2, 28),
        )
        db.add(budget)
        db.add_all(
            [
                Transaction(user_id=user_id, amount=Decimal("-125"), category="Food", occurred_on=date(2026, 2, 5)),
                Transaction(user_id=user_id, amount=Decimal("-75"), category="Food", occurred_on=date(2026, 2, 20)),
                Transaction(user_id=user_id, amount=Decimal("-500"), category="Rent", occurred_on=date(2026, 2, 5)),
            ]
        )

    with database() as db:
        stored_budget = db.query(Budget).filter_by(user_id=user_id).one()
        food_spending = sum(
            abs(row.amount)
            for row in db.query(Transaction).filter_by(user_id=user_id, category="Food").all()
        )

    assert stored_budget.limit_amount == Decimal("300.00")
    assert food_spending == Decimal("200")
    assert stored_budget.limit_amount - food_spending == Decimal("100.00")


def test_investment_valuation_calculates_profit_and_percentage(database, monkeypatch) -> None:
    user_id = uuid4()
    with database.begin() as db:
        db.add(
            InvestmentHolding(
                user_id=user_id,
                symbol="AAPL",
                quantity=Decimal("2"),
                average_cost=Decimal("100"),
                currency="USD",
            )
        )

    monkeypatch.setattr(
        "app.agents.investment_analyser.get_ticker",
        lambda symbol: SimpleNamespace(info={"currentPrice": 125}),
    )
    with database() as db:
        result = _portfolio_history_reply(db, user_id)

    assert "Current: 125.00 USD" in result.reply
    assert "Unrealized P/L: +50.00 USD (+25.00%)" in result.reply
    assert result.metadata["valued_holdings"] == "1"


def test_investment_valuation_handles_missing_market_data(database, monkeypatch) -> None:
    user_id = uuid4()
    with database.begin() as db:
        db.add(
            InvestmentHolding(
                user_id=user_id,
                symbol="MISSING",
                quantity=Decimal("1"),
                average_cost=Decimal("80"),
                currency="USD",
            )
        )

    monkeypatch.setattr("app.agents.investment_analyser.get_ticker", lambda symbol: SimpleNamespace(info={}))
    with database() as db:
        result = _portfolio_history_reply(db, user_id)

    assert "Current market price unavailable" in result.reply
    assert result.metadata["market_data"] == "unavailable"


def test_market_data_returns_history_and_handles_api_failure(monkeypatch) -> None:
    history = pd.DataFrame({"Close": [100.0, 105.0]})
    ticker = SimpleNamespace(history=lambda **kwargs: history)
    monkeypatch.setattr(market_data, "get_ticker", lambda symbol: ticker)
    assert market_data.fetch_history("AAPL", period="1mo").equals(history)

    failing_ticker = SimpleNamespace(history=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("rate limited")))
    monkeypatch.setattr(market_data, "get_ticker", lambda symbol: failing_ticker)
    assert market_data.fetch_history("INVALID", period="1mo") is None


def test_market_data_rejects_empty_external_response(monkeypatch) -> None:
    monkeypatch.setattr(market_data, "get_ticker", lambda symbol: SimpleNamespace(history=lambda **kwargs: pd.DataFrame()))
    assert market_data.fetch_history("EMPTY", period="5d") is None