"""Budget Planner agent: authoritative transaction aggregates and bounded LLM explanations."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.finance_context import extract_monthly_income
from app.agents.types import AgentName, AgentResult
from app.config import settings
from app.db.models import Transaction
from app.ml.finmate import finalize_llm_reply, generate, llm_available
from app.services.spending_insights import category_delta_vs_prior_month

_AMOUNT = r"(\d+(?:,\d{3})*(?:\.\d+)?)"


def _parse_labeled_amount(message: str, labels: str) -> Decimal | None:
    pattern = re.compile(
        rf"\b(?:{labels})\b\s*(?:is|are|of|equals|:|=)?\s*"
        rf"(?:(?:INR|Rs\.?|₹|USD|\$|EUR|€|GBP|£)\s*)?{_AMOUNT}",
        re.I,
    )
    match = pattern.search(message)
    return Decimal(match.group(1).replace(",", "")) if match else None


def _extract_balance_remainder(message: str) -> tuple[Decimal, Decimal, dict[str, Decimal]] | None:
    """Calculate explicitly itemized income-minus-expenses questions without an LLM."""
    if not re.search(r"\b(remain(?:s|ing)?|left|surplus)\b", message, re.I):
        return None

    income = _parse_labeled_amount(message, r"(?:monthly\s+)?(?:income|salary)")
    if income is None:
        return None

    expense_labels = {
        "rent": r"rent",
        "EMI": r"emi",
        "groceries": r"groceries?",
        "utilities": r"utilities?",
        "food": r"food",
        "insurance": r"insurance",
        "loan payment": r"loan payments?",
        "subscriptions": r"subscriptions?",
        "transport": r"transport(?:ation)?",
    }
    expenses: dict[str, Decimal] = {}
    for label, pattern in expense_labels.items():
        amount = _parse_labeled_amount(message, pattern)
        if amount is not None:
            expenses[label] = amount

    if not expenses:
        return None
    spent = sum(expenses.values(), Decimal("0"))
    return income - spent, income, expenses


def _requested_transaction_category(message: str, category_totals: dict[str, Decimal]) -> str | None:
    """Find an exact category named in a transaction-total question."""
    request = message.lower()
    asks_for_total = (
        bool(re.search(r"\bhow much\b", request))
        and bool(re.search(r"\b(spend|spent|spending|expense|expenses|transaction|transactions)\b", request))
    ) or bool(re.search(r"\b(total|sum)\b.*\b(spend|spent|spending|expense|expenses)\b", request))
    if not asks_for_total:
        return None

    matches = [
        category
        for category in sorted(category_totals, key=len, reverse=True)
        if category != "uncategorized"
        and re.search(rf"(?<![a-z]){re.escape(category)}(?![a-z])", request)
    ]
    # Do not silently answer a multi-category question with only one category.
    return matches[0] if len(matches) == 1 else None


def _format_amount(value: Decimal) -> str:
    return f"{value:,.2f}"


def run(
    user_id: UUID,
    message: str,
    db: Session,
    rag_context: str | None = None,
) -> AgentResult:
    # Straight arithmetic supplied explicitly in the request is deterministic.
    balance = _extract_balance_remainder(message)
    if balance is not None:
        remaining, income, expenses = balance
        currency_match = re.search(r"\b(INR|USD|EUR|GBP)\b|[₹$€£]", message, re.I)
        symbol = "INR" if currency_match and currency_match.group(0).upper() in {"INR", "₹"} else (
            currency_match.group(0).upper() if currency_match else "INR"
        )
        expense_text = "; ".join(
            f"{name}: {symbol} {_format_amount(amount)}" for name, amount in expenses.items()
        )
        reply = (
            "[AGENT: BUDGET]\n\n"
            f"Monthly income: {symbol} {_format_amount(income)}. "
            f"Expenses: {expense_text}. "
            f"Amount remaining after these expenses: {symbol} {_format_amount(remaining)}.\n\n"
            '{"intent":"budget_arithmetic","steps":["Parse stated income","Sum stated expenses","Subtract expenses from income"],'
            '"tools_needed":[],"notes":"exact arithmetic from user-provided figures"}'
        )
        return AgentResult(
            agent=AgentName.BUDGET_PLANNER,
            reply=reply,
            planned_steps=["parse_explicit_amounts", "calculate_income_less_expenses"],
            metadata={"source": "exact_arithmetic", "numeric_task": "income_minus_expenses"},
        )

    today = date.today()
    start = today - timedelta(days=30)

    currency = (
        db.scalar(
            select(Transaction.currency).where(Transaction.user_id == user_id).limit(1)
        )
        or "USD"
    )

    rows = db.execute(
        select(Transaction.category, func.coalesce(func.sum(Transaction.amount), 0))
        .where(Transaction.user_id == user_id, Transaction.occurred_on >= start)
        .group_by(Transaction.category)
    ).all()

    by_cat: dict[str, Decimal] = {}
    category_display: dict[str, str] = {}
    for cat, total in rows:
        display = (cat or "uncategorized").strip()
        key = display.casefold()
        category_display.setdefault(key, display)
        by_cat[key] = by_cat.get(key, Decimal("0")) + Decimal(str(total))

    # Category-specific totals are calculated by SQL, not inferred by the model.
    requested_category = _requested_transaction_category(message, by_cat)
    if requested_category is not None:
        matching_rows = db.execute(
            select(Transaction.currency, func.coalesce(func.sum(Transaction.amount), 0))
            .where(
                Transaction.user_id == user_id,
                Transaction.occurred_on >= start,
                func.lower(Transaction.category) == requested_category,
            )
            .group_by(Transaction.currency)
        ).all()
        if matching_rows:
            totals = [(str(cur or currency), Decimal(str(total))) for cur, total in matching_rows]
            if len(totals) == 1:
                result_currency, total = totals[0]
                prose = (
                    f"You spent {result_currency} {_format_amount(total)} on {requested_category} "
                    "in the last 30 days, based on transactions recorded in your account."
                )
            else:
                prose = (
                    f"Your {requested_category} spending in the last 30 days is split by currency: "
                    + "; ".join(f"{cur} {_format_amount(total)}" for cur, total in totals)
                    + ". These currencies are not combined."
                )
            reply = (
                "[AGENT: BUDGET]\n\n"
                f"{prose}\n\n"
                '{"intent":"transaction_category_total","steps":["Filter transactions by date and category","Sum amounts by currency"],'
                '"tools_needed":["list_transactions"],"notes":"computed from database transactions"}'
            )
            return AgentResult(
                agent=AgentName.BUDGET_PLANNER,
                reply=reply,
                planned_steps=["filter_transactions_30d_by_category", "sum_category_amounts"],
                metadata={
                    "source": "transaction_aggregate",
                    "window_days": "30",
                    "category": requested_category,
                    "numeric_currency_groups": str(len(totals)),
                },
            )

    total_flow = sum(by_cat.values(), start=Decimal("0"))
    top = sorted(by_cat.items(), key=lambda x: abs(x[1]), reverse=True)[:8]
    lines = [f"- {category_display.get(k, k)}: {v} {currency}" for k, v in top]

    if not lines:
        data_summary = "No transactions found in the last 30 days."
    else:
        data_summary = "Last 30 days by category:\n" + "\n".join(lines)
        data_summary += f"\nNet total: {total_flow} {currency}"

    mom = category_delta_vs_prior_month(db, user_id)
    if mom:
        data_summary += "\n\n" + mom

    rag_block = ""
    if rag_context and rag_context.strip():
        rag_block = "\n\n[Past context]\n" + rag_context.strip()[:2000]

    income, income_currency = extract_monthly_income(message, rag_context)
    budget_currency = income_currency or currency

    def _income_budget_reply() -> str:
        assert income is not None
        needs = (income * Decimal("0.5")).quantize(Decimal("0.01"))
        wants = (income * Decimal("0.3")).quantize(Decimal("0.01"))
        savings = (income * Decimal("0.2")).quantize(Decimal("0.01"))
        return (
            "[AGENT: BUDGET]\n\n"
            f"Using your stated monthly income of {income:,.2f} {budget_currency}, "
            f"a simple 50/30/20 split gives roughly {needs:,.2f} for essentials, "
            f"{wants:,.2f} for flexible spending, and {savings:,.2f} for savings or debt payoff.\n\n"
            "I don't have transaction history yet, so import CSV from Settings or add expenses "
            "when you can — then I can compare actual spending to these caps category by category.\n\n"
            '{"intent":"budget_plan","steps":["Set caps from income split","Import transactions","Review categories weekly"],'
            '"tools_needed":["list_transactions","set_budget"],"notes":"income-based plan; no transaction data"}'
        )

    def _db_reply() -> str:
        if not lines:
            if income is not None:
                return _income_budget_reply()
            return (
                "[AGENT: BUDGET]\n\n"
                "I don't see any transactions in the last 30 days yet. "
                "Share your monthly income or complete onboarding, and import CSV from Settings "
                "so I can analyze real spending.\n\n"
                '{"intent":"budget_plan","steps":["Import or add transactions","Review categories","Set weekly caps"],'
                '"tools_needed":["list_transactions"],"notes":"no transaction data"}'
            )
        top_lines = "\n".join(lines[:5])
        mom_block = f"\n\n{mom}" if mom else ""
        return (
            "[AGENT: BUDGET]\n\n"
            f"Here is your actual spending picture for the last 30 days (net {total_flow} {currency}):\n"
            f"{top_lines}{mom_block}\n\n"
            "Focus cuts on the largest absolute categories first, then set a weekly cap on the top variable line "
            "and move a fixed amount to savings on payday.\n\n"
            '{"intent":"budget_plan","steps":["Review top categories above","Cap largest variable category","Automate savings"],'
            '"tools_needed":["list_transactions","set_budget"],"notes":"built from DB aggregates"}'
        )

    source = "db_aggregate_fallback"
    if settings.finmate_use_llm and llm_available():
        enriched_message = (
            f"{message}\n\n"
            f"[User financial data]\n{data_summary}{rag_block}"
        )
        try:
            reply = finalize_llm_reply(
                generate(
                    enriched_message
                    + "\n\nThis is the BUDGET specialist flow. Start with exactly [AGENT: BUDGET]. "
                    "Treat the financial data above as authoritative. Do not invent or recalculate absent values. "
                    "When the question asks for arithmetic, calculate only from the stated figures.",
                    json_tools_fallback=["list_transactions", "set_budget"],
                )
            )
            source = "llm_with_db_context"
        except Exception:
            reply = _db_reply()
    else:
        reply = _db_reply()
        source = "db_aggregates"

    agent_meta: dict[str, str] = {
        "window_days": "30",
        "categories_found": str(len(by_cat)),
        "source": source,
    }
    if income is not None:
        agent_meta["income_detected"] = f"{income:,.2f} {budget_currency}"

    return AgentResult(
        agent=AgentName.BUDGET_PLANNER,
        reply=reply,
        planned_steps=["load_transactions_30d", "aggregate_by_category", "mom_insights", "retrieve_rag", "finmate_generate"],
        metadata=agent_meta,
    )
