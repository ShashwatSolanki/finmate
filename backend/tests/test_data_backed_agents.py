"""Regression tests for data-backed investment and invoice behavior."""

import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.agents.agentic_orchestrator import run_agentic_turn
from app.agents.budget_planner import (
    _asks_for_investable_surplus,
    _asks_for_transaction_total,
    run as run_budget,
)
from app.agents.invoice_generator import (
    _expense_reporting_window,
    _invoice_from_memory_text,
    _wants_expense_invoice,
    _wants_invoice_output,
    _references_uploaded_invoice,
    _structured_from_message,
    run as run_invoice,
)
from app.agents.investment_analyser import (
    _SymbolAnalysis,
    _extract_lump_sum,
    _extract_risk_from_context,
    run as run_investment,
)
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

    def test_lump_sum_parser_recognizes_allocating_an_amount(self):
        prompt = "Assess whether allocating INR 10,000 to Microsoft fits my monthly budget."
        self.assertEqual(_extract_lump_sum(prompt), Decimal("10000"))

    def test_portfolio_comparison_with_ticker_preserves_saved_portfolio(self):
        portfolio = AgentResult(
            AgentName.INVESTMENT_ANALYSER,
            "[AGENT: INVESTMENT]\n\nPortfolio Snapshot\n\n"
            "AAPL | 10 units | Avg cost: 100.00 USD | Current: 125.00 USD | "
            "P/L: +250.00 USD (+25.00%)\n\n"
            "Total invested (valued holdings): 1,000.00 USD\n"
            "Current value: 1,250.00 USD\n"
            "Unrealized P/L: +250.00 USD (+25.00%)\nHoldings valued: 1/1",
            metadata={
                "tickers": "AAPL",
                "source": "portfolio_holdings",
                "holdings_count": "1",
                "valued_holdings": "1",
            },
        )
        analysis = _SymbolAnalysis(
            "MSFT",
            True,
            "Microsoft Corporation (MSFT)\n"
            "Latest available session: 2026-10-12\n"
            "Last close: 541.00 USD (+1.00, +0.18% vs previous session)\n"
            "20-day SMA: 510.00 — trading above the short-term average.",
            Decimal("541.00"),
            Decimal("510.00"),
            Decimal("0.18"),
        )

        with (
            patch("app.agents.investment_analyser.pick_validated_tickers", return_value=["MSFT"]),
            patch("app.agents.investment_analyser._portfolio_history_reply", return_value=portfolio),
            patch("app.agents.investment_analyser._analyze_symbol", return_value=analysis),
        ):
            result = run_investment(
                self.user_id,
                "Review my saved investment portfolio and compare it with Microsoft (MSFT). "
                "Show my existing MSFT exposure if any, the latest market snapshot, and concentration risks.",
                self.db,
            )

        self.assertEqual(result.metadata["source"], "portfolio_market_comparison")
        self.assertIn("Saved portfolio snapshot", result.reply)
        self.assertIn("Requested market comparison", result.reply)
        self.assertIn("Last close: 541.00 USD", result.reply)
        self.assertIn("no existing holding is recorded", result.reply)
        self.assertNotIn("[tokenized_tools]", result.reply)
        self.assertNotIn('{"intent"', result.reply)

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


    def test_combined_agent_invoice_clause_preserves_all_items_without_colons(self):
        prompt = (
            "Complete all three tasks. First, report my last-30-day recorded expenses by category and keep "
            "uploaded receipts separate from transactions. Second, review my saved AAPL, MSFT, and NVDA portfolio. "
            "Third, create an invoice from Shashwat Solanki to Acme Technologies: Website Development 2 units at "
            "INR 12,000 each, Hosting 1 unit at INR 2,000, SEO Audit 1 unit at INR 3,000, with 18% tax. "
            "Preserve the invoice fields for export."
        )
        invoice = _structured_from_message(prompt)

        self.assertIsNotNone(invoice)
        assert invoice is not None
        self.assertEqual(invoice.vendor_name, "Shashwat Solanki")
        self.assertEqual(invoice.bill_to, "Acme Technologies")
        self.assertEqual(
            [(item.description, item.quantity, item.unit_price, item.amount) for item in invoice.line_items],
            [
                ("Website Development", 2.0, Decimal("12000"), Decimal("24000.00")),
                ("Hosting", 1.0, Decimal("2000"), Decimal("2000.00")),
                ("SEO Audit", 1.0, Decimal("3000"), Decimal("3000.00")),
            ],
        )
        self.assertEqual(invoice.subtotal, Decimal("29000.00"))
        self.assertEqual(invoice.tax, Decimal("5220.00"))
        self.assertEqual(invoice.total, Decimal("34220.00"))

    def test_financial_data_audit_does_not_trigger_expense_invoice_generation(self):
        prompt = (
            "Audit my financial data before giving any recommendation. Identify which figures are directly "
            "supported by recorded transactions, which are from my uploaded receipt, which are from market quotes, "
            "and which are missing. Do not treat an uploaded receipt as a transaction, do not assume unrecorded "
            "expenses are zero, and do not invent monthly totals or currency conversions."
        )
        self.assertFalse(_wants_expense_invoice(prompt))
        self.assertFalse(_wants_invoice_output(prompt))

        result = run_invoice(self.user_id, prompt, self.db)

        self.assertEqual(result.metadata["source"], "prompt")
        self.assertNotIn("invoice_actions", result.metadata)
        self.assertNotIn("invoice_payload", result.metadata)
        self.assertIn("I could not detect invoice line items", result.reply)

    def test_explicit_invoice_with_spending_context_keeps_text_source(self):
        result = run_invoice(
            self.user_id,
            "Summarize my monthly spending and prepare an invoice for "
            "configuration INR 400 plus support INR 500.",
            self.db,
        )

        self.assertEqual(result.metadata["source"], "structured_parse")
        self.assertIn('"notes": "parsed from message text"', result.reply)
        self.assertNotIn('"notes": "generated from recent user transactions"', result.reply)

    def test_client_invoice_clause_in_multi_agent_request_keeps_clean_item_name(self):
        invoice = _structured_from_message(
            "Review my budget, estimate investable surplus, and invoice a client "
            "for development INR 3600."
        )

        self.assertIsNotNone(invoice)
        assert invoice is not None
        self.assertEqual(invoice.currency, "INR")
        self.assertEqual(
            [(item.description, item.amount) for item in invoice.line_items],
            [("development", Decimal("3600.00"))],
        )
        self.assertEqual(invoice.subtotal, Decimal("3600.00"))
        self.assertEqual(invoice.total, Decimal("3600.00"))

    def test_invoice_preserves_and_inside_descriptions_and_grouped_amounts(self):
        invoice = _structured_from_message(
            "Create invoice for design and development INR 1,200 and hosting INR 2,000."
        )

        self.assertIsNotNone(invoice)
        assert invoice is not None
        self.assertEqual(
            [(item.description, item.amount) for item in invoice.line_items],
            [
                ("design and development", Decimal("1200.00")),
                ("hosting", Decimal("2000.00")),
            ],
        )
        self.assertEqual(invoice.subtotal, Decimal("3200.00"))
        self.assertEqual(invoice.total, Decimal("3200.00"))

    def test_full_natural_language_invoice_preserves_all_items_and_tax(self):
        invoice = _structured_from_message(
            "Create an invoice from Shashwat Solanki to Acme Technologies, due 30 days from today. "
            "Add Website Development: 2 units at INR 12,000 each, "
            "Hosting: 1 unit at INR 2,000, and SEO Audit: 1 unit at INR 3,000. "
            "Apply 18% tax to the subtotal."
        )

        self.assertIsNotNone(invoice)
        assert invoice is not None
        self.assertEqual(invoice.vendor_name, "Shashwat Solanki")
        self.assertEqual(invoice.bill_to, "Acme Technologies")
        self.assertEqual(invoice.due_date, (date.today() + timedelta(days=30)).isoformat())
        self.assertEqual(invoice.currency, "INR")
        self.assertEqual(
            [
                (item.description, item.quantity, item.unit_price, item.amount)
                for item in invoice.line_items
            ],
            [
                ("Website Development", 2.0, Decimal("12000"), Decimal("24000.00")),
                ("Hosting", 1.0, Decimal("2000"), Decimal("2000.00")),
                ("SEO Audit", 1.0, Decimal("3000"), Decimal("3000.00")),
            ],
        )
        self.assertEqual(invoice.subtotal, Decimal("29000.00"))
        self.assertEqual(invoice.tax, Decimal("5220.00"))
        self.assertEqual(invoice.total, Decimal("34220.00"))

    def test_uploaded_receipt_memory_reconstructs_the_full_invoice(self):
        memory_text = """User: Uploaded invoice file: test_bill.jpg
Source type: image; extraction confidence: high.
Invoice date: 28/11/25
Vendor: The Hole in wall Cafe
Line items:
- Chicken Fajita Breakfast Tacos: 300.00 INR
- French Toast With Lemon Cream Cheese And Blueberry Compote: 190.00 INR
- The Breakfast Bake: 210.00 INR
- Tiramisu Those Pancakes: 280.00 INR
Subtotal: 980.00 INR
Tax: 49.00 INR
Total: 1029.00 INR
"""
        invoice = _invoice_from_memory_text(memory_text)

        self.assertIsNotNone(invoice)
        assert invoice is not None
        self.assertEqual(invoice.vendor_name, "The Hole in wall Cafe")
        self.assertEqual(invoice.currency, "INR")
        self.assertEqual([item.amount for item in invoice.line_items], [
            Decimal("300.00"), Decimal("190.00"), Decimal("210.00"), Decimal("280.00")
        ])
        self.assertEqual(invoice.subtotal, Decimal("980.00"))
        self.assertEqual(invoice.tax, Decimal("49.00"))
        self.assertEqual(invoice.total, Decimal("1029.00"))
        self.assertTrue(_references_uploaded_invoice("Review the invoice or receipt I uploaded."))
        self.assertTrue(
            _references_uploaded_invoice(
                "Summarize my recorded expenses for the previous calendar month and prepare a report. "
                "Clearly separate actual transactions from any uploaded invoice or receipt."
            )
        )

    def test_uploaded_receipt_followup_uses_saved_invoice_instead_of_parsing_prompt(self):
        saved_invoice = _invoice_from_memory_text(
            """User: Uploaded invoice file: test_bill.jpg
Source type: image; extraction confidence: high.
Vendor: The Hole in wall Cafe
Line items:
- Chicken Fajita Breakfast Tacos: 300.00 INR
- French Toast With Lemon Cream Cheese And Blueberry Compote: 190.00 INR
- The Breakfast Bake: 210.00 INR
- Tiramisu Those Pancakes: 280.00 INR
Subtotal: 980.00 INR
Tax: 49.00 INR
Total: 1029.00 INR
"""
        )
        with (
            patch("app.agents.invoice_generator._invoice_from_recent_upload", return_value=saved_invoice),
            patch("app.agents.invoice_generator.settings.finmate_use_llm", False),
        ):
            result = run_invoice(
                self.user_id,
                "Review the invoice or receipt I uploaded and show the extracted total.",
                self.db,
            )

        self.assertEqual(result.metadata["source"], "uploaded_invoice_context")
        self.assertEqual(result.metadata["parsed_items_count"], "4")
        self.assertEqual(result.metadata["parsed_total"], "1029.00")
        self.assertIn("The Hole in wall Cafe", result.reply)
        self.assertIn("1029.00 INR", result.reply)

    def test_previous_calendar_month_window_uses_complete_calendar_boundaries(self):
        today = date.today()
        month_start = date(today.year, today.month, 1)
        expected_end = month_start - timedelta(days=1)
        expected_start = date(expected_end.year, expected_end.month, 1)

        start, end, label = _expense_reporting_window(
            "Summarize my recorded expenses for the previous calendar month."
        )

        self.assertEqual((start, end), (expected_start, expected_end))
        self.assertIn("previous calendar month", label)

    def test_expense_invoice_without_transactions_explains_missing_data_for_requested_period(self):
        self.db.scalars.return_value.all.return_value = []
        with patch("app.agents.invoice_generator.settings.finmate_use_llm", False):
            result = run_invoice(
                self.user_id,
                "Summarize my recorded expenses for the previous calendar month and prepare an invoice-style report.",
                self.db,
            )

        start, end, _label = _expense_reporting_window("previous calendar month")
        self.assertEqual(result.metadata["source"], "missing_transaction_history")
        self.assertIn("no matching recorded expense transactions", result.reply.lower())
        self.assertIn("previous calendar month", result.reply.lower())
        self.assertEqual(result.metadata["period_start"], start.isoformat())
        self.assertEqual(result.metadata["period_end"], end.isoformat())
        self.assertNotIn("could not detect invoice line items", result.reply.lower())

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


    def test_spending_report_sums_expense_debits_as_positive_and_includes_requested_count(self):
        prompt = (
            "Using only my recorded transactions for the last 30 days, show total spending by category in INR. "
            "Include Food, Rent, Utilities, Transport, and an overall total. "
            "Report the transaction count and exact date range."
        )
        today = date.today()
        with (
            patch(
                "app.agents.budget_planner._aggregate_expenses",
                return_value={
                    "INR": {
                        "Food": Decimal("3450"),
                        "Rent": Decimal("12000"),
                        "Utilities": Decimal("1600"),
                        "Transport": Decimal("900"),
                    }
                },
            ),
            patch("app.agents.budget_planner._count_expenses", return_value=15),
            patch("app.agents.budget_planner.extract_monthly_income", return_value=(None, None)),
        ):
            result = run_budget(self.user_id, prompt, self.db)

        self.assertEqual(result.metadata["source"], "deterministic_spending_report")
        self.assertIn("Food: 3,450.00 INR", result.reply)
        self.assertIn("Total recorded expenses in INR: 17,950.00", result.reply)
        self.assertIn("Recorded expense transaction count: 15", result.reply)
        self.assertIn((today - timedelta(days=29)).isoformat(), result.reply)
        self.assertIn(today.isoformat(), result.reply)
        self.assertNotIn("-3450.00", result.reply)
        self.assertNotIn("net -", result.reply.casefold())

    def test_month_comparison_uses_calendar_months_and_correct_change_direction(self):
        prompt = (
            "Compare my previous complete calendar month with the complete calendar month before it. "
            "Show category totals, absolute changes, and percentage changes."
        )
        newer = {"INR": {"Food": Decimal("2200"), "Rent": Decimal("12000")}}
        older = {"INR": {"Food": Decimal("2000"), "Rent": Decimal("11500")}}
        with patch(
            "app.agents.budget_planner._aggregate_expenses",
            side_effect=[newer, older],
        ):
            result = run_budget(self.user_id, prompt, self.db)

        self.assertEqual(result.metadata["source"], "deterministic_month_comparison")
        self.assertIn("Food: September 2026 2,200.00; August 2026 2,000.00; increased by 200.00 INR (+10.00%)", result.reply)
        self.assertIn("Rent: September 2026 12,000.00; August 2026 11,500.00; increased by 500.00 INR (+4.35%)", result.reply)
        self.assertIn("TOTAL: 14,200.00 vs 13,500.00 INR; increased by 700.00 INR (+5.19%)", result.reply)

    def test_budget_plan_computes_negative_remainder_and_warns_about_missing_costs(self):
        prompt = (
            "My saved monthly income is INR 50,000. Create a needs, wants, and savings budget, "
            "and compare it with my actual recorded monthly expenses. Distinguish the simple remainder "
            "from genuinely investable surplus."
        )
        with (
            patch(
                "app.agents.budget_planner._aggregate_expenses",
                return_value={
                    "INR": {
                        "Rent": Decimal("62500"),
                        "Food": Decimal("17250"),
                        "Utilities": Decimal("8000"),
                        "Transport": Decimal("4500"),
                        "Healthcare": Decimal("1750"),
                    }
                },
            ),
            patch(
                "app.agents.budget_planner.extract_monthly_income",
                return_value=(Decimal("50000"), "INR"),
            ),
        ):
            result = run_budget(self.user_id, prompt, self.db)

        self.assertEqual(result.metadata["source"], "deterministic_budget_comparison")
        self.assertIn("Stated monthly income: 50,000.00 INR", result.reply)
        self.assertIn("Simple remainder after recorded expenses", result.reply)
        self.assertIn("-44,000.00 INR", result.reply)
        self.assertIn("not guaranteed investable surplus", result.reply.lower())

    def test_no_transaction_history_does_not_turn_spending_report_into_budget_split(self):
        self.db.scalar.return_value = None
        self.db.execute.return_value.all.return_value = []
        self.db.scalars.return_value.all.return_value = []

        with (
            patch("app.agents.budget_planner.category_delta_vs_prior_month", return_value=None),
            patch(
                "app.agents.budget_planner.extract_monthly_income",
                return_value=(Decimal("50000"), "INR"),
            ),
            patch("app.agents.budget_planner.settings.finmate_use_llm", False),
        ):
            result = run_budget(
                self.user_id,
                "Compare my spending in the most recent complete calendar month with the month before it. "
                "Show category totals and percentage changes where mathematically valid.",
                self.db,
            )

        self.assertEqual(result.metadata["source"], "deterministic_month_comparison")
        self.assertIn("not mean your spending was zero", result.reply)
        self.assertNotIn("50/30/20", result.reply)

    def test_investable_surplus_prompt_is_not_misclassified_as_category_total(self):
        prompt = (
            "Using my saved monthly income and recorded expenses, estimate how much money is available "
            "for monthly investing without assuming missing expenses are zero. Assess whether allocating "
            "INR 10,000 to Microsoft is consistent with that budget."
        )
        self.assertFalse(_asks_for_transaction_total(prompt))
        self.assertTrue(_asks_for_investable_surplus(prompt))
        self.assertTrue(_asks_for_transaction_total("How much did I spend on food in the last 30 days?"))

    def test_missing_expense_data_prevents_invented_investable_surplus(self):
        self.db.scalar.return_value = None
        self.db.execute.return_value.all.return_value = []
        self.db.scalars.return_value.all.return_value = []

        prompt = (
            "Using my saved monthly income and recorded expenses, estimate how much money is available "
            "for monthly investing without assuming missing expenses are zero. Assess whether allocating "
            "INR 10,000 to Microsoft is consistent with that budget."
        )
        with (
            patch("app.agents.budget_planner.category_delta_vs_prior_month", return_value=None),
            patch(
                "app.agents.budget_planner.extract_monthly_income",
                return_value=(Decimal("50000"), "INR"),
            ),
            patch("app.agents.budget_planner.settings.finmate_use_llm", False),
        ):
            result = run_budget(self.user_id, prompt, self.db)

        self.assertEqual(result.metadata["source"], "deterministic_budget_comparison")
        self.assertIn("investable surplus", result.reply.lower())
        self.assertIn("50,000.00 INR", result.reply)
        self.assertIn("cannot safely be treated as zero", result.reply)
        self.assertNotIn("50/30/20", result.reply)
        self.assertNotIn("single spending category", result.reply)

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
