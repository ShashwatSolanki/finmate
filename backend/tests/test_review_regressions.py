from decimal import Decimal
from datetime import date
import uuid

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import pytest
from pydantic import ValidationError

from app.agents.investment_analyser import _extract_lump_sum
from app.agents.invoice_generator import _detect_currency, _structured_from_message
from app.api.routes.budgets import BudgetUpdate
from app.api.routes.transactions import _safe_csv_cell, monthly_summary
from app.config import settings
from app.db.base import Base
from app.db.models import Transaction, User
from app.api.routes.auth import _verify_google_credential


def test_simple_invoice_keeps_explicit_currency() -> None:
    invoice = _structured_from_message("Create invoice for ₹1200 website design")
    assert invoice is not None
    assert invoice.currency == "INR"
    assert invoice.total == Decimal("1200.00")


def test_currency_detection_handles_common_symbols() -> None:
    assert _detect_currency("Charge €40 for design") == "EUR"
    assert _detect_currency("Bill £40 for design") == "GBP"
    assert _detect_currency("Charge $40 for design") == "USD"


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("I'm 22 and want to invest ₹100,000", Decimal("100000")),
        ("I want to invest 100000", Decimal("100000")),
        ("Put a lump sum of 2 lakh into the plan", Decimal("200000")),
    ],
)
def test_lump_sum_parser_ignores_age_and_reads_amount(prompt: str, expected: Decimal) -> None:
    assert _extract_lump_sum(prompt) == expected


@pytest.mark.parametrize("value", ["=1+1", "+SUM(A1:A2)", "@SUM(A1:A2)", "  =1+1"])
def test_csv_text_cells_are_safe_for_spreadsheets(value: str) -> None:
    assert _safe_csv_cell(value).startswith("'")


@pytest.mark.parametrize("payload", [{"period_start": None}, {"period_end": None}, {"limit_amount": None}, {"category": None}])
def test_budget_patch_rejects_explicit_null_for_required_fields(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        BudgetUpdate.model_validate(payload)



def test_monthly_summary_counts_only_expenses_and_respects_currency() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(id=uuid.uuid4(), email="summary@example.com", password_hash="hashed", is_verified=True)
        db.add(user)
        db.add_all([
            Transaction(user_id=user.id, amount=Decimal("-500.00"), currency="INR", category="Food", occurred_on=date(2026, 10, 5)),
            Transaction(user_id=user.id, amount=Decimal("2000.00"), currency="INR", category="Income", occurred_on=date(2026, 10, 6)),
        ])
        db.commit()
        result = monthly_summary(year=2026, month=10, currency=None, db=db, current=user)
        assert result.total_expenses == Decimal("500.00")
        assert result.currency == "INR"
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_google_mock_tokens_are_not_accepted_outside_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "auth_allow_mock_google", True)
    monkeypatch.setattr(settings, "google_client_id", None)
    with pytest.raises(HTTPException) as error:
        _verify_google_credential("mock-google-token:attacker@example.com:fake-sub")
    assert error.value.status_code == 503
