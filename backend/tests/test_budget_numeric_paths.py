"""Regression tests for exact numeric paths in the budget specialist."""

import unittest
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from app.agents.budget_planner import (
    _extract_balance_remainder,
    _requested_transaction_category,
    run,
)
from app.db.models import Transaction, User


class BudgetNumericPathTests(unittest.TestCase):
    def test_balance_remainder_uses_all_explicit_expenses(self):
        parsed = _extract_balance_remainder(
            "My monthly income is INR 38000, rent is INR 9000, EMI is INR 2500, "
            "groceries are INR 3000, and utilities are INR 1000. "
            "How much remains after these expenses?"
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        remaining, income, expenses = parsed
        self.assertEqual(income, Decimal("38000"))
        self.assertEqual(sum(expenses.values()), Decimal("15500"))
        self.assertEqual(remaining, Decimal("22500"))

    def test_balance_remainder_supports_comma_grouped_amounts(self):
        parsed = _extract_balance_remainder(
            "Monthly income is INR 51,750, rent is INR 10,875, EMI is INR 3,125, "
            "groceries are INR 3,550, and utilities are INR 1,225. "
            "What amount remains?"
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed[0], Decimal("32975"))

    def test_balance_helper_ignores_non_arithmetic_budget_advice(self):
        self.assertIsNone(_extract_balance_remainder("Help me create a monthly budget from my income."))

    def test_balance_remainder_supports_indian_grouping(self):
        parsed = _extract_balance_remainder(
            "Monthly income is INR 1,00,000, rent is INR 25,000, EMI is INR 10,000. "
            "How much remains after these expenses?"
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed[0], Decimal("65000"))

    def test_balance_remainder_scales_lakh_suffix(self):
        parsed = _extract_balance_remainder(
            "Monthly income is INR 1 lakh, rent is INR 25,000, EMI is INR 10,000. "
            "How much remains after these expenses?"
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed[1], Decimal("100000"))
        self.assertEqual(parsed[0], Decimal("65000"))

    def test_balance_remainder_rejects_mixed_currency_arithmetic(self):
        self.assertIsNone(
            _extract_balance_remainder(
                "Monthly income is INR 1,00,000, rent is USD 250. "
                "How much remains after these expenses?"
            )
        )

    def test_malformed_grouping_is_not_partially_parsed(self):
        self.assertIsNone(
            _extract_balance_remainder(
                "Monthly income is INR 1,00,00, rent is INR 5,000. How much remains?"
            )
        )

    def test_malformed_four_digit_group_is_not_partially_parsed(self):
        self.assertIsNone(
            _extract_balance_remainder(
                "Monthly income is INR 50,0000, rent is INR 5,000. How much remains?"
            )
        )

    def test_balance_remainder_refuses_unrecognized_currency_expense(self):
        self.assertIsNone(
            _extract_balance_remainder(
                "Monthly income is INR 50,000, rent is INR 10,000, "
                "credit card bill is INR 5,000. How much remains?"
            )
        )

    def test_balance_remainder_refuses_unrecognized_expense_without_currency(self):
        self.assertIsNone(
            _extract_balance_remainder(
                "Monthly income is 50000, rent is 10000, "
                "credit card bill is 5000. How much remains?"
            )
        )

    def test_category_total_requires_a_transaction_total_question(self):
        categories = {"food": Decimal("825"), "transport": Decimal("777")}
        self.assertEqual(
            _requested_transaction_category(
                "How much did I spend on food in the last 30 days?", categories
            ),
            "food",
        )
        self.assertIsNone(_requested_transaction_category("Help me set a food budget.", categories))
        self.assertIsNone(
            _requested_transaction_category(
                "How much did I spend on food and transport?", categories
            )
        )
        # A category should still be recognized when it has no recent rows.
        self.assertEqual(
            _requested_transaction_category(
                "How much did I spend on food in the last 30 days?", {}
            ),
            "food",
        )
        self.assertEqual(
            _requested_transaction_category(
                "How much did I spend on food in the last 30 days?",
                {"groceries": Decimal("0")},
            ),
            "groceries",
        )
        self.assertIsNone(
            _requested_transaction_category(
                "How much did I spend on food and transport?", {"food": Decimal("0")}
            )
        )


def test_category_spending_uses_expense_debit_signs(database):
    user_id = uuid4()
    with database.begin() as db:
        db.add(User(id=user_id, email=f"{user_id}@example.test", password_hash="test-hash"))
        db.add_all(
            [
                Transaction(
                    user_id=user_id, amount=Decimal("-500"), currency="INR",
                    category="food", description="groceries", occurred_on=date.today(),
                ),
                Transaction(
                    user_id=user_id, amount=Decimal("-325"), currency="INR",
                    category="food", description="dining", occurred_on=date.today(),
                ),
                Transaction(
                    user_id=user_id, amount=Decimal("200"), currency="INR",
                    category="food", description="refund", occurred_on=date.today(),
                ),
            ]
        )

    with database() as db:
        result = run(user_id, "How much did I spend on food in the last 30 days?", db)

    assert "You spent INR 825.00 on food" in result.reply
    assert "-825.00" not in result.reply


def test_missing_category_does_not_fall_back_to_unrelated_income_budget(database):
    user_id = uuid4()
    with database.begin() as db:
        db.add(User(id=user_id, email=f"{user_id}@example.test", password_hash="test-hash"))
        db.add(
            Transaction(
                user_id=user_id,
                amount=Decimal("-3600"),
                currency="INR",
                category="Rent",
                occurred_on=date.today() - timedelta(days=60),
            )
        )

    with database() as db:
        result = run(
            user_id,
            "How much did I spend on food in the last 30 days?",
            db,
            rag_context="Monthly income: 50000 INR",
        )

    assert "couldn't find any recorded Food expenses in the last 30 days" in result.reply
    assert "50/30/20" not in result.reply
    assert result.metadata["source"] == "transaction_aggregate_no_match"
    assert result.metadata["window_days"] == "30"


def test_multi_category_question_requests_clarification_instead_of_budget_advice(database):
    user_id = uuid4()
    with database.begin() as db:
        db.add(User(id=user_id, email=f"{user_id}@example.test", password_hash="test-hash"))
        db.add(
            Transaction(
                user_id=user_id,
                amount=Decimal("-1200"),
                currency="INR",
                category="Rent",
                occurred_on=date.today(),
            )
        )

    with database() as db:
        result = run(
            user_id,
            "How much did I spend on food and transport?",
            db,
            rag_context="Monthly income: 50000 INR",
        )

    assert "single spending category" in result.reply
    assert result.metadata["source"] == "transaction_query_clarification"
    assert "50/30/20" not in result.reply


if __name__ == "__main__":
    unittest.main()
