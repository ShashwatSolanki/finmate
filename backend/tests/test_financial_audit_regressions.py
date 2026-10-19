"""Regression tests for source-aware financial audits and export consistency."""

import json
from datetime import date
from decimal import Decimal
from uuid import uuid4
from unittest.mock import MagicMock

from app.agents.agentic_orchestrator import build_plan
from app.agents.budget_planner import (
    _duplicate_expense_summary_lines,
    _period_data_sufficiency_lines,
)
from app.agents.investment_analyser import _market_quote_evidence_lines
from app.agents.invoice_generator import run as run_invoice
from app.db.models import Transaction


def test_duplicate_summary_quantifies_repeated_expense_rows_without_mutating(database):
    user_id = uuid4()
    with database.begin() as db:
        db.add_all(
            [
                Transaction(
                    user_id=user_id,
                    amount=Decimal("-1200.00"),
                    currency="INR",
                    category="Food",
                    description="October groceries",
                    occurred_on=date(2026, 10, 8),
                )
                for _ in range(5)
            ]
        )

    with database() as db:
        before = db.query(Transaction).filter(Transaction.user_id == user_id).count()
        lines = _duplicate_expense_summary_lines(
            db,
            user_id,
            date(2026, 10, 1),
            date(2026, 10, 31),
            label="October 2026",
        )
        after = db.query(Transaction).filter(Transaction.user_id == user_id).count()

    rendered = "\n".join(lines)
    assert "1 repeated pattern(s) affect 5 recorded row(s)" in rendered
    assert "4 additional matching row(s)" in rendered
    assert "4,800.00 INR in repeated-copy value" in rendered
    assert before == after == 5


def test_empty_period_is_explicitly_insufficient_not_zero_spending(database):
    user_id = uuid4()
    with database() as db:
        lines = _period_data_sufficiency_lines(
            db,
            user_id,
            date(2026, 8, 1),
            date(2026, 8, 31),
            label="August 2026",
        )

    assert "INSUFFICIENT" in "\n".join(lines)
    assert "Actual spending is unknown, not zero" in "\n".join(lines)


def test_market_audit_extracts_quotes_from_recent_context_without_fx_conversion():
    context = """Requested market comparison:

Latest available market snapshot:
Apple Inc. (AAPL)
Latest available session: 2026-10-16
Last close: 343.46 USD (+1.46, +0.43% vs previous session)
20-day SMA: 337.42 — trading above the short-term average.
Microsoft Corporation (MSFT)
Latest available session: 2026-10-16
Last close: 529.05 USD (-10.39, -1.93% vs previous session)
20-day SMA: 520.22 — trading above the short-term average.
NVIDIA Corporation (NVDA)
Latest available session: 2026-10-16
Last close: 214.13 USD (-3.10, -1.43% vs previous session)
20-day SMA: 228.18 — trading below the short-term average.
Existing ticker exposure:
- AAPL: existing holding value is approximately 1,717.30 USD.
"""
    evidence = _market_quote_evidence_lines(context)
    rendered = "\n".join(evidence)

    assert len(evidence) == 3
    assert "AAPL" in rendered and "343.46 USD" in rendered
    assert "MSFT" in rendered and "529.05 USD" in rendered
    assert "NVDA" in rendered and "214.13 USD" in rendered
    assert all("2026-10-16" in line for line in evidence)


def test_provenance_audit_plan_includes_uploaded_receipt_agent():
    prompt = (
        "Audit my financial data before giving any recommendation. Identify which figures are "
        "directly supported by recorded transactions, which are from my uploaded receipt, "
        "which are from market quotes, and which are missing."
    )
    plan = build_plan(prompt)

    assert plan is not None
    agents = {step.agent.value for step in plan.steps}
    assert "budget_planner" in agents
    assert "investment_analyser" in agents
    assert "invoice_generator" in agents


def test_invoice_reference_matches_serialized_payload_for_pdf_and_csv_export():
    prompt = (
        "Create an invoice from Shashwat Solanki to Acme Technologies: "
        "Website Development 2 units at INR 12,000 each, Hosting 1 unit at INR 2,000, "
        "and SEO Audit 1 unit at INR 3,000, with 18% tax."
    )
    result = run_invoice(uuid4(), prompt, MagicMock())
    payload = json.loads(result.metadata["invoice_payload"])

    assert result.metadata["invoice_ref"] == payload["invoice_number"]
    assert f"Invoice ref: #{payload['invoice_number']}" in result.reply
    assert payload["subtotal"] == "29000.00" or Decimal(str(payload["subtotal"])) == Decimal("29000.00")
    assert Decimal(str(payload["tax"])) == Decimal("5220.00")
    assert Decimal(str(payload["total"])) == Decimal("34220.00")
