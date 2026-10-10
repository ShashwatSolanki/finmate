"""Regression tests for data-backed investment and invoice behavior."""

import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.agents.agentic_orchestrator import run_agentic_turn
from app.agents.budget_planner import run as run_budget
from app.agents.invoice_generator import _structured_from_message, run as run_invoice
from app.agents.investment_analyser import _extract_risk_from_context, run as run_investment
from app.agents.types import AgentName, AgentResult


class DataBackedAgentTests(unittest.TestCase):
    def test_risk_profile_parser_accepts_sentence_form_from_memory(self):
        self.assertEqual(
            _extract_risk_from_context(
                "The synthetic user's investment risk tolerance is conservative."
            ),
            "conservative",
        )

    def setUp(self):
        self.db = MagicMock()
        self.user_id = uuid4()

    def test_missing_risk_context_is_labelled_as_illustrative(self):
        result = run_investment(
            self.user_id,
            "How should I invest my surplus?",
            self.db,
            rag_context=None,
        )

        self.assertIn("couldn't verify your saved risk tolerance", result.reply.lower())
        self.assertIn("illustrative, not personalized", result.reply.lower())
        self.assertNotIn("using your moderate risk profile", result.reply.lower())
        self.assertEqual(result.metadata["source"], "illustrative_default")

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
        ticker.info = {"currentPrice": 125, "currency": "USD"}

        with patch("app.agents.investment_analyser.get_ticker", return_value=ticker):
            result = run_investment(
                self.user_id,
                "What are my current investment profits?",
                self.db,
            )

        self.assertEqual(result.metadata["source"], "portfolio_holdings")
        self.assertEqual(result.metadata["holdings_count"], "1")
        self.assertIn("Unrealized P/L: +250.00 USD (+25.00%)", result.reply)

    def test_plus_separated_invoice_items_survive_multi_domain_prompt(self):
        invoice = _structured_from_message(
            "Summarize monthly spending and prepare an invoice for "
            "configuration INR 400 plus support INR 500."
        )

        self.assertIsNotNone(invoice)
        assert invoice is not None
        self.assertEqual(invoice.currency, "INR")
        self.assertEqual(
            [(item.description, item.amount) for item in invoice.line_items],
            [
                ("configuration", Decimal("400.00")),
                ("support", Decimal("500.00")),
            ],
        )
        self.assertEqual(invoice.subtotal, Decimal("900.00"))
        self.assertEqual(invoice.total, Decimal("900.00"))

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

    def test_budget_without_transactions_skips_llm_generation(self):
        self.db.scalar.return_value = None
        self.db.execute.return_value.all.return_value = []

        with (
            patch(
                "app.agents.budget_planner.category_delta_vs_prior_month",
                return_value=None,
            ),
            patch(
                "app.agents.budget_planner.extract_monthly_income",
                return_value=(None, None),
            ),
            patch("app.agents.budget_planner.settings.finmate_use_llm", True),
            patch("app.agents.budget_planner.llm_available", return_value=True),
            patch("app.agents.budget_planner.generate") as model_generate,
        ):
            result = run_budget(
                self.user_id,
                "Summarize my recent spending by category and suggest a budget cap.",
                self.db,
            )

        self.assertEqual(result.metadata["source"], "db_aggregates")
        self.assertIn("don't see any transactions", result.reply)
        model_generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
