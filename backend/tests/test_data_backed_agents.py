"""Regression tests for data-backed investment and invoice behavior."""

import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.agents.agentic_orchestrator import run_agentic_turn
from app.agents.invoice_generator import run as run_invoice
from app.agents.investment_analyser import run as run_investment
from app.agents.types import AgentName, AgentResult


class DataBackedAgentTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.user_id = uuid4()

    def test_investment_history_does_not_invent_portfolio(self):
        self.db.scalars.return_value.all.return_value = []
        result = run_investment(
            self.user_id,
            "What are my last investments profits?",
            self.db,
        )
        self.assertEqual(result.metadata["source"], "no_investment_data")
        self.assertIn("don't have any stored investment holdings", result.reply)

    def test_portfolio_history_uses_stored_holdings_and_market_price(self):
        holding = MagicMock()
        holding.symbol = "AAPL"
        holding.quantity = Decimal("10")
        holding.average_cost = Decimal("100")
        holding.currency = "USD"
        self.db.scalars.return_value.all.return_value = [holding]

        ticker = MagicMock()
        ticker.info = {"currentPrice": 125}

        with patch("app.agents.investment_analyser.get_ticker", return_value=ticker):
            result = run_investment(
                self.user_id,
                "What are my current investment profits?",
                self.db,
            )

        self.assertEqual(result.metadata["source"], "portfolio_holdings")
        self.assertEqual(result.metadata["holdings_count"], "1")
        self.assertIn("Unrealized P/L: +250.00 USD (+25.00%)", result.reply)

    def test_expense_invoice_uses_recent_transactions(self):
        invoice = MagicMock()
        item = MagicMock()
        item.description = "Food"
        item.amount = Decimal("1887")
        item.quantity = None
        item.unit_price = None
        invoice.line_items = [item]
        invoice.currency = "INR"
        invoice.total = Decimal("1887")
        invoice.subtotal = Decimal("1887")
        invoice.tax = None
        invoice.vendor_name = None
        invoice.bill_to = None
        invoice.invoice_date = None
        invoice.invoice_number = "EXP-20260920"
        invoice.due_date = None
        invoice.notes = "test"
        invoice.model_dump_json.return_value = '{"line_items":[{"description":"Food","amount":"1887"}]}'

        with (
            patch(
                "app.agents.invoice_generator._expense_invoice_from_transactions",
                return_value=invoice,
            ),
            patch("app.agents.invoice_generator.settings.finmate_use_llm", False),
        ):
            result = run_invoice(
                self.user_id,
                "Generate an invoice for my expenses.",
                self.db,
            )

        self.assertEqual(result.metadata["invoice_actions"], "pdf,csv")
        self.assertEqual(result.metadata["source"], "transaction_summary")

    def test_agentic_pipeline_preserves_invoice_export_payload(self):
        invoice = AgentResult(
            agent=AgentName.INVOICE_GENERATOR,
            reply="[AGENT: INVOICE] Invoice ready.",
            metadata={
                "invoice_ref": "EXP-20260920",
                "invoice_payload": '{"line_items":[{"description":"Food","amount":"100"}]}',
                "invoice_actions": "pdf,csv",
                "parsed_items_count": "1",
            },
        )

        with (
            patch(
                "app.agents.agentic_orchestrator.budget_planner.run",
                return_value=AgentResult(AgentName.BUDGET_PLANNER, "[AGENT: BUDGET] budget"),
            ),
            patch(
                "app.agents.agentic_orchestrator.investment_analyser.run",
                return_value=AgentResult(AgentName.INVESTMENT_ANALYSER, "[AGENT: INVESTMENT] investment"),
            ),
            patch(
                "app.agents.agentic_orchestrator.invoice_generator.run",
                return_value=invoice,
            ),
            patch("app.agents.agentic_orchestrator.settings.finmate_use_llm", False),
        ):
            result = run_agentic_turn(
                self.user_id,
                "Analyze my spending, tell me how much I can invest, and generate an invoice.",
                self.db,
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.metadata["invoice_ref"], "EXP-20260920")
        self.assertIn("invoice_payload", result.metadata)
        self.assertEqual(result.metadata["invoice_actions"], "pdf,csv")


if __name__ == "__main__":
    unittest.main()
