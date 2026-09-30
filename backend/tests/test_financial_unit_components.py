from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agents.finance_context import extract_monthly_income
from app.agents.intent import classify_agent
from app.agents.ticker_utils import (
    extract_ticker_candidates,
    has_investment_signal,
    pick_validated_tickers,
)
from app.agents.types import AgentName
from app.api.routes.transactions import _parse_csv_amount, _parse_csv_date
from app.security.jwt_tokens import (
    create_access_token,
    create_refresh_token,
    decode_token_subject,
)
from app.security.passwords import hash_password, validate_password_strength, verify_password
from app.services import market_data
from app.services.spending_insights import category_delta_vs_prior_month


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("My monthly income is INR 75,000", (Decimal("75000"), "INR")),
        ("salary $4,200", (Decimal("4200"), None)),
        ("I earn 3,500 every month", (None, None)),
        ("Nothing about income here", (None, None)),
    ],
)
def test_finance_context_extracts_income_and_currency(message: str, expected: tuple[Decimal | None, str | None]) -> None:
    assert extract_monthly_income(message) == expected


def test_finance_context_prefers_onboarding_context() -> None:
    assert extract_monthly_income("Help me budget", "monthly income: 80000 INR") == (Decimal("80000"), "INR")


def test_ticker_extraction_handles_dollar_symbols_company_names_and_caps() -> None:
    assert extract_ticker_candidates("Compare $aapl, Microsoft, and TSLA") == ["AAPL", "MSFT", "TSLA"]
    assert extract_ticker_candidates("What is the price of my food budget?") == []
    assert has_investment_signal("Should I invest in an index fund?") is True
    assert has_investment_signal("How much did I spend on rent?") is False


def test_validated_ticker_extraction_filters_unavailable_symbols(monkeypatch) -> None:
    monkeypatch.setattr("app.services.market_data.has_price_series", lambda symbol: symbol == "AAPL")
    assert pick_validated_tickers("Compare AAPL and TSLA") == ["AAPL"]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("How much did I spend on groceries?", AgentName.BUDGET_PLANNER),
        ("Generate an itemized invoice", AgentName.INVOICE_GENERATOR),
        ("Analyze AAPL stock trend", AgentName.INVESTMENT_ANALYSER),
        ("", AgentName.BUDGET_PLANNER),
    ],
)
def test_intent_classification_uses_hard_domain_signals(message: str, expected: AgentName, monkeypatch) -> None:
    monkeypatch.setattr("app.agents.intent._embedding_vector", lambda text: {agent: 0.5 for agent in AgentName})
    assert classify_agent(message) == expected


@pytest.mark.parametrize(
    ("password", "valid"),
    [
        ("ValidPass1!", True),
        ("short1!", False),
        ("nouppercase1!", False),
        ("NoDigits!", False),
        ("NoSpecial1", False),
    ],
)
def test_password_strength_policy(password: str, valid: bool) -> None:
    is_valid, error = validate_password_strength(password)
    assert is_valid is valid
    assert (error is None) is valid


def test_password_hash_verifies_without_storing_plaintext() -> None:
    plain = "ValidPass1!"
    hashed = hash_password(plain)
    assert hashed != plain
    assert verify_password(plain, hashed) is True
    assert verify_password("WrongPass1!", hashed) is False


def test_jwt_access_and_refresh_types_are_distinct() -> None:
    user_id = uuid4()
    access = create_access_token(user_id)
    refresh, _, _ = create_refresh_token(user_id)
    assert decode_token_subject(access) == user_id
    assert decode_token_subject(refresh) is None


def test_jwt_rejects_malformed_or_wrong_subject() -> None:
    assert decode_token_subject("not-a-token") is None
    token = create_access_token(uuid4(), extra_claims={"sub": "not-a-uuid"})
    assert decode_token_subject(token) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("$1,250.50", Decimal("1250.50")), ("(75.00)", Decimal("-75.00")), ("Rs. 10", Decimal("10"))],
)
def test_csv_amount_parser_handles_currency_and_negative_formats(raw: str, expected: Decimal) -> None:
    assert _parse_csv_amount(raw) == expected


def test_csv_parsers_reject_malformed_values() -> None:
    with pytest.raises(ValueError, match="invalid amount"):
        _parse_csv_amount("abc")
    with pytest.raises(ValueError, match="unsupported date"):
        _parse_csv_date("2026.01.01")


def test_spending_insights_empty_data_returns_empty(database) -> None:
    with database() as db:
        assert category_delta_vs_prior_month(db, uuid4(), ref=date(2026, 3, 1)) == ""


def test_spending_insights_ignores_other_users_and_small_changes(database) -> None:
    user_id = uuid4()
    with database.begin() as db:
        from app.db.models import Transaction

        db.add_all(
            [
                Transaction(user_id=user_id, amount=Decimal("100"), category="Food", occurred_on=date(2026, 1, 1)),
                Transaction(user_id=user_id, amount=Decimal("104"), category="Food", occurred_on=date(2026, 2, 1)),
                Transaction(user_id=uuid4(), amount=Decimal("999"), category="Food", occurred_on=date(2026, 2, 1)),
            ]
        )
    with database() as db:
        assert category_delta_vs_prior_month(db, user_id, ref=date(2026, 3, 1)) == ""


def test_market_data_cache_and_empty_response(monkeypatch) -> None:
    market_data.has_price_series.cache_clear()
    calls = []

    def history(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(empty=False)

    monkeypatch.setattr(market_data, "fetch_history", lambda symbol, period="5d": history())
    assert market_data.has_price_series("AAPL", period="5d") is True
    assert market_data.has_price_series("AAPL", period="5d") is True
    assert len(calls) == 1

    monkeypatch.setattr(market_data, "fetch_history", lambda symbol, period="5d": None)
    market_data.has_price_series.cache_clear()
    assert market_data.has_price_series("MISSING") is False