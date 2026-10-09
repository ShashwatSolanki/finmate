from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.agents.investment_analyser import _extract_lump_sum
from app.agents.invoice_generator import _detect_currency, _structured_from_message
from app.api.routes.budgets import BudgetUpdate
from app.api.routes.transactions import _safe_csv_cell


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
