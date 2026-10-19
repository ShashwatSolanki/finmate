"""Regression tests for data-backed investment and invoice behavior."""

import json
import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.agents.agentic_orchestrator import (
    _synthesize,
    _visible_specialist_observation,
    build_plan,
    run_agentic_turn,
)
from app.agents.budget_planner import (
    _asks_for_investable_surplus,
    _asks_for_spending_report,
    _asks_for_transaction_total,
    _deterministic_spending_report,
    _is_month_comparison_request,
    _original_user_request,
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
from app.invoice.schemas import ParsedLineItem


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

    def test_agentic_synthesis_rejects_raw_tool_trace_and_keeps_clean_agents(self):
        raw_budget = AgentResult(
            AgentName.BUDGET_PLANNER,
            "[AGENT: BUDGET]\n\nBudget Planner:\n"
            '[\n"Agent says: Risk assessment needs date and asset valuation first.", '
            '"tools_needed=[date_set, tool_name=budget_recommendation]"\n]',
            metadata={"source": "llm"},
        )
        verified_investment = AgentResult(
            AgentName.INVESTMENT_ANALYSER,
            "[AGENT: INVESTMENT]\n\n"
            "Microsoft Corporation (MSFT)\n"
            "Latest available session: 2026-10-16\n"
            "Last close: 529.05 USD\n20-day SMA: 520.22\n\n"
            '{"intent":"investment_info","tools_needed":["yfinance_lookup"],"notes":"test"}',
            metadata={"source": "verified_data_fallback"},
        )

        self.assertIsNone(_visible_specialist_observation(raw_budget.reply))
        with patch("app.agents.agentic_orchestrator.settings.finmate_use_llm", False):
            reply = _synthesize(
                "Audit transactions and compare Microsoft with my portfolio.",
                [raw_budget, verified_investment],
                AgentName.BUDGET_PLANNER,
            )

        self.assertIn("did not produce a readable, validated answer", reply)
        self.assertIn("Last close: 529.05 USD", reply)
        self.assertNotIn("Agent says:", reply)
        self.assertNotIn("tools_needed=", reply)
        self.assertNotIn("tool_name=", reply)


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

    def test_csv_transaction_audit_is_classified_as_deterministic_report(self):
        prompt = (
            "Imported 15 transaction(s) from finmate_test_transactions.csv.\\n\\n"
            "Audit my recorded transactions for the last 30 days. Report the date range, transaction count, "
            "total expenses, and totals by category in INR. Show how many transactions were imported from "
            "the most recent CSV and identify duplicate-looking rows, but do not delete or modify any transactions. "
            "Treat expense debits as positive spending in the report."
        )
        self.assertTrue(_asks_for_spending_report(prompt))
        self.assertFalse(_is_month_comparison_request(prompt))

        sentinel = AgentResult(
            AgentName.BUDGET_PLANNER,
            "deterministic audit",
            metadata={"source": "deterministic_spending_report"},
        )
        augmented = (
            prompt
            + "\\n\\n[Verified specialist observations]\\n"
            "Income 50000; prior result was a stock quote for CSV."
        )
        with patch(
            "app.agents.budget_planner._deterministic_spending_report",
            return_value=sentinel,
        ) as report:
            result = run_budget(self.user_id, augmented, self.db)

        self.assertIs(result, sentinel)
        report.assert_called_once_with(self.db, self.user_id, prompt, None)

    def test_complete_calendar_month_comparison_uses_positive_spending_and_correct_direction(self):
        prompt = (
            "Compare my previous complete calendar month with the complete calendar month before it. "
            "Show actual spending totals by category for both months, the absolute change, and percentage changes."
        )
        self.assertTrue(_is_month_comparison_request(prompt))

        newer = {"INR": {"Food": Decimal("11000"), "Transport": Decimal("4250")}}
        older = {"INR": {"Food": Decimal("10000"), "Transport": Decimal("4000")}}
        with (
            patch("app.agents.budget_planner._aggregate_expenses", side_effect=[newer, older]),
            patch("app.agents.budget_planner._duplicate_warning_lines", return_value=[]),
        ):
            result = _deterministic_spending_report(self.db, self.user_id, prompt)

        self.assertEqual(result.metadata["source"], "deterministic_month_comparison")
        self.assertIn("increased by 1,000.00 INR (+10.00%).", result.reply)
        self.assertIn("increased by 250.00 INR (+6.25%).", result.reply)
        self.assertNotIn("-10,000.00", result.reply)
        self.assertNotIn("-11,000.00", result.reply)

    def test_month_comparison_ignores_appended_specialist_observations(self):
        prompt = (
            "Compare my previous complete calendar month with the complete calendar month before it. "
            "Show category totals and the percentage change."
        )
        augmented = prompt + "\\n\\n[Verified specialist observations]\\nPrior month amount: INR 94000."
        sentinel = AgentResult(
            AgentName.BUDGET_PLANNER,
            "deterministic month comparison",
            metadata={"source": "deterministic_month_comparison"},
        )
        with patch(
            "app.agents.budget_planner._deterministic_spending_report",
            return_value=sentinel,
        ) as report:
            result = run_budget(self.user_id, augmented, self.db)

        self.assertIs(result, sentinel)
        report.assert_called_once_with(self.db, self.user_id, prompt, None)

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


    def test_combined_three_agent_invoice_request_exports_all_items_and_tax(self):
        prompt = (
            "Complete all three tasks. First, report my last-30-day recorded expenses by category and distinguish "
            "them from the café receipt I uploaded. Second, review my saved AAPL, MSFT, and NVDA portfolio. "
            "Third, create an invoice from Shashwat Solanki to Acme Technologies: Website Development 2 units at "
            "INR 12,000 each, Hosting 1 unit at INR 2,000, SEO Audit 1 unit at INR 3,000, with 18% tax. "
            "Preserve the invoice fields for export. Clearly label transaction data, receipt data, market data, "
            "and newly generated invoice data."
        )
        saved_receipt = _invoice_from_memory_text(
            """User: Uploaded invoice file: test_bill.jpg
Vendor: The Hole in wall Cafe
Line items:
- Chicken Fajita Breakfast Tacos: 300.00 INR
Subtotal: 300.00 INR
Tax: 15.00 INR
Total: 315.00 INR
"""
        )
        with patch("app.agents.invoice_generator._invoice_from_recent_upload", return_value=saved_receipt):
            result = run_invoice(self.user_id, prompt, self.db)
        payload = json.loads(result.metadata["invoice_payload"])

        self.assertEqual(result.metadata["source"], "structured_parse")
        self.assertEqual(result.metadata["parsed_items_count"], "3")
        self.assertEqual(result.metadata["parsed_total"], "34220.00")
        self.assertEqual(result.metadata["invoice_actions"], "pdf,csv")
        self.assertEqual(
            [(item["description"], item.get("quantity"), item.get("unit_price"), item["amount"])
             for item in payload["line_items"]],
            [
                ("Website Development", 2.0, "12000", "24000.00"),
                ("Hosting", 1.0, "2000", "2000.00"),
                ("SEO Audit", 1.0, "3000", "3000.00"),
            ],
        )
        self.assertEqual(payload["subtotal"], "29000.00")
        self.assertEqual(payload["tax"], "5220.00")
        self.assertEqual(payload["total"], "34220.00")

    def test_invoice_export_is_blocked_if_explicit_item_count_is_incomplete(self):
        prompt = (
            "Create an invoice from Shashwat Solanki to Acme Technologies: "
            "Website Development 2 units at INR 12,000 each, Hosting 1 unit at INR 2,000, "
            "SEO Audit 1 unit at INR 3,000, with 18% tax."
        )
        partial_items = [
            # Simulate a parser regression that missed two valid quantity/price clauses.
            ParsedLineItem(
                description="Hosting",
                quantity=1,
                unit_price=Decimal("2000"),
                amount=Decimal("2000"),
            )
        ]
        with (
            patch("app.agents.invoice_generator._explicit_items_from_request", return_value=partial_items),
            patch("app.agents.invoice_generator._invoice_from_recent_upload", return_value=None),
        ):
            result = run_invoice(self.user_id, prompt, self.db)

        self.assertEqual(result.metadata["source"], "incomplete_invoice_request")
        self.assertNotIn("invoice_payload", result.metadata)
        self.assertNotIn("invoice_actions", result.metadata)
        self.assertIn("not created or exported a partial invoice", result.reply)
        self.assertEqual(result.metadata["expected_item_clauses"], "3")


    def test_invoice_parser_preserves_intentionally_repeated_line_items(self):
        invoice = _structured_from_message(
            "Create an invoice: Consulting 1 unit at INR 500, Consulting 1 unit at INR 500, with 18% tax."
        )

        self.assertIsNotNone(invoice)
        assert invoice is not None
        self.assertEqual(len(invoice.line_items), 2)
        self.assertEqual([item.description for item in invoice.line_items], ["Consulting", "Consulting"])
        self.assertEqual(invoice.subtotal, Decimal("1000.00"))
        self.assertEqual(invoice.tax, Decimal("180.00"))
        self.assertEqual(invoice.total, Decimal("1180.00"))

    def test_financial_data_audit_uses_uploaded_receipt_without_creating_new_invoice(self):
        prompt = (
            "Audit my financial data before giving any recommendation. Identify which figures are directly "
            "supported by recorded transactions, which are from my uploaded receipt, which are from market quotes, "
            "and which are missing. Do not treat an uploaded receipt as a transaction, do not assume unrecorded "
            "expenses are zero, and do not invent monthly totals or currency conversions."
        )
        self.assertTrue(_references_uploaded_invoice(prompt))
        self.assertFalse(_wants_expense_invoice(prompt))
        self.assertFalse(_wants_invoice_output(prompt))

        saved_receipt = _invoice_from_memory_text(
            """User: Uploaded invoice file: test_bill.jpg
Source type: image; extraction confidence: medium.
Invoice number: 73430
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
Extraction warnings:
- The printed subtotal was not reliably read by OCR.
"""
        )
        self.assertIsNotNone(saved_receipt)
        with patch("app.agents.invoice_generator._invoice_from_recent_upload", return_value=saved_receipt):
            result = run_invoice(self.user_id, prompt, self.db)

        self.assertEqual(result.metadata["source"], "uploaded_invoice_context")
        self.assertNotIn("invoice_actions", result.metadata)
        self.assertNotIn("invoice_payload", result.metadata)
        self.assertIn("The Hole in wall Cafe", result.reply)
        self.assertIn("1029.00 INR", result.reply)
        self.assertIn("no new PDF was requested", result.reply)

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


    def test_financial_data_audit_does_not_schedule_an_invoice_artifact(self):
        prompt = (
            "Audit my financial data before giving any recommendation. Identify which figures are directly "
            "supported by recorded transactions, which are from my uploaded receipt, which are from market quotes, "
            "and which are missing. Do not treat an uploaded receipt as a transaction, do not assume unrecorded "
            "expenses are zero, and do not invent monthly totals or currency conversions."
        )
        plan = build_plan(prompt)

        self.assertIsNotNone(plan)
        assert plan is not None
        agents = [step.agent for step in plan.steps]
        self.assertIn(AgentName.BUDGET_PLANNER, agents)
        self.assertIn(AgentName.INVESTMENT_ANALYSER, agents)
        self.assertNotIn(AgentName.INVOICE_GENERATOR, agents)

    def test_agentic_fallback_rejects_raw_tool_traces(self):
        observations = [
            AgentResult(
                AgentName.BUDGET_PLANNER,
                '[AGENT: BUDGET]\n\n["Agent says: risk assessment", "tools_needed=[date_set]"]',
            ),
            AgentResult(
                AgentName.INVESTMENT_ANALYSER,
                "[AGENT: INVESTMENT]\n\nMSFT last close is 529.05 USD.",
            ),
        ]
        with patch("app.agents.agentic_orchestrator.settings.finmate_use_llm", False):
            reply = _synthesize(
                "Audit my budget and Microsoft exposure.",
                observations,
                AgentName.BUDGET_PLANNER,
            )

        self.assertNotIn("Agent says:", reply)
        self.assertNotIn("tools_needed=[", reply)
        self.assertIn("did not produce a readable, validated answer", reply)
        self.assertIn("MSFT last close is 529.05 USD.", reply)

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




    def test_category_total_surfaces_duplicate_warning_and_uses_positive_spend(self):
        prompt = "How much did I spend on food in the last 30 days?"
        category_rows = MagicMock()
        category_rows.all.return_value = [("Food", Decimal("17250"))]
        total_rows = MagicMock()
        total_rows.all.return_value = [("INR", Decimal("17250"))]
        self.db.scalar.return_value = "INR"
        self.db.execute.side_effect = [category_rows, total_rows]
        self.db.scalars.return_value.all.return_value = ["Food"]

        with patch(
            "app.agents.budget_planner._duplicate_warning_lines",
            return_value=[
                "Potential exact-match duplicate-looking records (not removed or excluded from totals):",
                "- 2026-10-10 | food | cafe lunch | 450.00 INR (expense debit) appears 5 times.",
            ],
        ):
            result = run_budget(self.user_id, prompt, self.db)

        self.assertEqual(result.metadata["source"], "transaction_aggregate")
        self.assertIn("You spent INR 17,250.00 on Food", result.reply)
        self.assertIn("appears 5 times", result.reply)
        self.assertIn("not removed or excluded from totals", result.reply)

    def test_spending_report_flags_exact_duplicate_looking_rows_without_removing_them(self):
        prompt = (
            "Audit my recorded transactions for the last 30 days and identify duplicate-looking rows. "
            "Do not delete or modify transactions."
        )
        with (
            patch(
                "app.agents.budget_planner._aggregate_expenses",
                return_value={"INR": {"Food": Decimal("2250")}},
            ),
            patch(
                "app.agents.budget_planner._potential_duplicate_rows",
                return_value=[
                    "- 2026-10-10 | food | cafe lunch | 450.00 INR (expense debit) appears 5 times."
                ],
            ),
            patch("app.agents.budget_planner.extract_monthly_income", return_value=(None, None)),
        ):
            result = run_budget(self.user_id, prompt, self.db)

        self.assertIn("Potential exact-match duplicate-looking records", result.reply)
        self.assertIn("appears 5 times", result.reply)
        self.assertIn("not removed or excluded from totals", result.reply)
        self.assertIn("Food: 2,250.00 INR", result.reply)

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
            patch("app.agents.budget_planner._count_transactions", return_value=15),
            patch("app.agents.budget_planner.extract_monthly_income", return_value=(None, None)),
        ):
            result = run_budget(self.user_id, prompt, self.db)

        self.assertEqual(result.metadata["source"], "deterministic_spending_report")
        self.assertIn("Food: 3,450.00 INR", result.reply)
        self.assertIn("Total recorded expenses in INR: 17,950.00", result.reply)
        self.assertIn("Recorded transaction count: 15", result.reply)
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
        newer_month_start = date.today().replace(day=1) - timedelta(days=1)
        older_month_end = newer_month_start.replace(day=1) - timedelta(days=1)
        newer_label = newer_month_start.strftime("%B %Y")
        older_label = older_month_end.strftime("%B %Y")
        self.assertIn(f"Food: {newer_label} 2,200.00; {older_label} 2,000.00; increased by 200.00 INR (+10.00%)", result.reply)
        self.assertIn(f"Rent: {newer_label} 12,000.00; {older_label} 11,500.00; increased by 500.00 INR (+4.35%)", result.reply)
        self.assertIn("TOTAL: 14,200.00 vs 13,500.00 INR; increased by 700.00 INR (+5.19%)", result.reply)


    def test_budget_with_expenses_but_missing_income_does_not_crash_or_invent_remainder(self):
        prompt = (
            "Create a needs, wants, and savings budget and compare it with my actual recorded expenses."
        )
        with (
            patch(
                "app.agents.budget_planner._aggregate_expenses",
                return_value={"INR": {"Food": Decimal("3450")}},
            ),
            patch(
                "app.agents.budget_planner.extract_monthly_income",
                return_value=(None, None),
            ),
        ):
            result = run_budget(self.user_id, prompt, self.db)

        self.assertEqual(result.metadata["source"], "deterministic_budget_comparison")
        self.assertIn("Monthly income was not found", result.reply)
        self.assertNotIn("Simple remainder after recorded expenses", result.reply)

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
        self.assertIn("does not mean spending was zero", result.reply)
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

        self.assertEqual(result.metadata["source"], "deterministic_budget_comparison")
        self.assertIn("No recorded expense debits were found", result.reply)
        self.assertIn("Monthly income was not found", result.reply)
        model_generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
