"""Regression tests for exact numeric paths in the budget specialist."""

import unittest
from decimal import Decimal

from app.agents.budget_planner import _extract_balance_remainder, _requested_transaction_category


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

    def test_category_total_requires_a_transaction_total_question(self):
        categories = {"food": Decimal("825"), "transport": Decimal("777")}
        self.assertEqual(
            _requested_transaction_category(
                "How much did I spend on food in the last 30 days?", categories
            ),
            "food",
        )
        self.assertIsNone(_requested_transaction_category("Help me set a food budget.", categories))


if __name__ == "__main__":
    unittest.main()
