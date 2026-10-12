"""Budget Planner agent: authoritative transaction aggregates and bounded LLM explanations."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.agents.finance_context import extract_monthly_income
from app.agents.types import AgentName, AgentResult
from app.config import settings
from app.db.models import Transaction
from app.ml.finmate import finalize_llm_reply, generate, llm_available
from app.services.spending_insights import category_delta_vs_prior_month

_AMOUNT = r"((?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3}|\d+)(?:\.\d+)?)(?:\s*(k|m|thousands?|millions?|lakhs?|crores?)\b)?"
def _parse_labeled_amount(message: str, labels: str) -> Decimal | None:
    pattern = re.compile(
        rf"\b(?:{labels})\b\s*(?:is|are|of|equals|:|=)?\s*"
        rf"(?:(?:INR|Rs\.?|₹|USD|\$|EUR|€|GBP|£)\s*)?{_AMOUNT}(?!\d|,\d)",
        re.I,
    )
    match = pattern.search(message)
    if not match:
        return None
    amount = Decimal(match.group(1).replace(",", ""))
    magnitude = (match.group(2) or "").lower()
    multiplier = (
        Decimal("1000") if magnitude in {"k", "thousand", "thousands"} else
        Decimal("1000000") if magnitude in {"m", "million", "millions"} else
        Decimal("100000") if magnitude in {"lakh", "lakhs"} else
        Decimal("10000000") if magnitude in {"crore", "crores"} else
        Decimal("1")
    )
    return amount * multiplier


def _currency_codes(message: str) -> set[str]:
    """Return normalized explicit currency markers present in an arithmetic request."""
    markers = re.findall(r"\b(?:INR|USD|EUR|GBP|Rs\.?)\b|[₹$€£]", message, re.I)
    aliases = {
        "INR": "INR", "RS": "INR", "RS.": "INR", "₹": "INR",
        "USD": "USD", "$": "USD",
        "EUR": "EUR", "€": "EUR",
        "GBP": "GBP", "£": "GBP",
    }
    return {aliases.get(marker.upper(), aliases.get(marker, marker.upper())) for marker in markers}


def _extract_balance_remainder(message: str) -> tuple[Decimal, Decimal, dict[str, Decimal]] | None:
    """Calculate explicitly itemized income-minus-expenses questions without an LLM."""
    if not re.search(r"\b(remain(?:s|ing)?|left|surplus)\b", message, re.I):
        return None

    # Subtraction across currencies is undefined without an exchange-rate conversion.
    if len(_currency_codes(message)) > 1:
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

    # Do not silently ignore an amount just because its label is outside the
    # supported expense vocabulary (for example, a credit-card bill). Require each
    # numeric amount in the request to map to income or one supported expense.
    # This deliberately falls back on more ambiguous requests rather than reporting
    # an overstated remainder. Unrelated numerals (such as a time horizon) can also
    # trigger the fallback, which is safer than guessing.
    amount_mentions = list(
        re.finditer(rf"(?<![A-Za-z0-9,.]){_AMOUNT}(?!\d|,\d)", message, re.I)
    )
    if len(amount_mentions) > 1 + len(expenses):
        return None
    if not expenses:
        return None
    spent = sum(expenses.values(), Decimal("0"))
    return income - spent, income, expenses


_CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "food": ("food", "groceries", "grocery", "dining", "restaurant", "restaurants"),
    "rent": ("rent", "housing"),
    "transport": ("transport", "transportation", "travel", "fuel", "commute"),
    "utilities": ("utilities", "utility", "electricity", "water", "internet"),
    "healthcare": ("healthcare", "health", "medical", "pharmacy"),
    "shopping": ("shopping", "clothing"),
    "entertainment": ("entertainment", "subscriptions", "subscription"),
}


def _asks_for_transaction_total(message: str) -> bool:
    request = message.lower()
    return (
        bool(re.search(r"\bhow much\b", request))
        and bool(re.search(r"\b(spend|spent|spending|expense|expenses|transaction|transactions)\b", request))
    ) or bool(re.search(r"\b(total|sum)\b.*\b(spend|spent|spending|expense|expenses)\b", request))


def _contains_category_term(text: str, term: str) -> bool:
    return bool(re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", text.casefold()))


def _requested_transaction_category(message: str, category_totals: dict[str, Decimal]) -> str | None:
    """Resolve one category even when it has no recent transactions.

    category_totals may include zero-valued historical category names. That lets
    the caller distinguish no activity in the reporting window from no such
    category in recent aggregates.
    """
    request = message.casefold()
    if not _asks_for_transaction_total(request):
        return None

    categories = [
        category.casefold()
        for category in category_totals
        if category.casefold() != "uncategorized"
    ]
    matched_groups: list[tuple[str, tuple[str, ...]]] = []
    for canonical, aliases in _CATEGORY_ALIASES.items():
        triggered = tuple(alias for alias in aliases if _contains_category_term(request, alias))
        if triggered:
            matched_groups.append((canonical, triggered))

    # Detect multiple requested domains before matching against stored categories.
    # Otherwise 'food and transport' could be reduced to 'food' when there are no
    # stored transport rows to match.
    if len(matched_groups) > 1:
        return None

    matches = [
        category
        for category in sorted(set(categories), key=len, reverse=True)
        if _contains_category_term(request, category)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return None
    if len(matched_groups) != 1:
        return None

    canonical, _ = matched_groups[0]
    # Once the user's category intent is known, use the full synonym group to
    # match recorded category labels (e.g. 'food' should find 'Groceries').
    aliases = _CATEGORY_ALIASES[canonical]
    observed = [
        category
        for category in sorted(set(categories), key=len, reverse=True)
        if any(_contains_category_term(category, alias) for alias in aliases)
    ]
    if len(observed) == 1:
        return observed[0]
    if len(observed) > 1:
        return None
    # The category can be absent from all stored records. Return its intent so
    # the caller can report missing data instead of changing the subject.
    return canonical


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

    # Include historical category names for intent resolution; the spending
    # query still applies the requested 30-day reporting window.
    historical_categories = db.scalars(
        select(Transaction.category)
        .where(Transaction.user_id == user_id, Transaction.category.is_not(None))
        .distinct()
    ).all()
    category_catalog = {
        str(category).strip().casefold(): Decimal("0")
        for category in historical_categories
        if str(category).strip()
    }
    category_catalog.update(by_cat)

    # Category-specific totals are calculated by SQL, never inferred by the model.
    requested_category = _requested_transaction_category(message, category_catalog)
    if (
        requested_category is None
        and _asks_for_transaction_total(message)
        and re.search(r"\b(?:on|for)\s+[a-z]", message, re.I)
    ):
        reply = (
            "[AGENT: BUDGET]\n\n"
            "I couldn't resolve that as a single spending category. Please ask about one category at a time, "
            "or ask for a spending summary to see the categories recorded in your account. "
            "I won't substitute an unrelated budget split for a transaction-total question.\n\n"
            '{"intent":"transaction_category_clarification","steps":["Resolve requested category","Query recorded expenses"],'
            '"tools_needed":["list_transactions"],"notes":"request includes an ambiguous or unsupported category"}'
        )
        return AgentResult(
            agent=AgentName.BUDGET_PLANNER,
            reply=reply,
            planned_steps=["resolve_single_spending_category", "request_clarification"],
            metadata={"source": "transaction_query_clarification", "window_days": "30"},
        )
    if requested_category is not None:
        matching_rows = db.execute(
            select(
                Transaction.currency,
                func.coalesce(
                    func.sum(case((Transaction.amount < 0, -Transaction.amount), else_=0)),
                    0,
                ),
            )
            .where(
                Transaction.user_id == user_id,
                Transaction.occurred_on >= start,
                func.lower(func.trim(Transaction.category)) == requested_category,
                Transaction.amount < 0,
            )
            .group_by(Transaction.currency)
        ).all()
        requested_display = category_display.get(requested_category, requested_category.title())
        if not matching_rows:
            prose = (
                f"I couldn't find any recorded {requested_display} expenses in the last 30 days. "
                "That is not an all-time total; older transactions or transactions filed under a different "
                "category are outside this result. I won't estimate spending from unrelated categories."
            )
            reply = (
                "[AGENT: BUDGET]\n\n"
                f"{prose}\n\n"
                '{"intent":"transaction_category_total","steps":["Filter transactions by date and category","Report no matching expense records"],'
                '"tools_needed":["list_transactions"],"notes":"no matching category expenses in the requested window"}'
            )
            return AgentResult(
                agent=AgentName.BUDGET_PLANNER,
                reply=reply,
                planned_steps=["filter_transactions_30d_by_category", "report_no_matching_expenses"],
                metadata={
                    "source": "transaction_aggregate_no_match",
                    "window_days": "30",
                    "category": requested_category,
                    "numeric_currency_groups": "0",
                },
            )

        totals = [(str(cur or currency), Decimal(str(total))) for cur, total in matching_rows]
        if len(totals) == 1:
            result_currency, total = totals[0]
            prose = (
                f"You spent {result_currency} {_format_amount(total)} on {requested_display} "
                "in the last 30 days, based on recorded debit transactions."
            )
        else:
            prose = (
                f"Your {requested_display} spending in the last 30 days is split by currency: "
                + "; ".join(f"{cur} {_format_amount(total)}" for cur, total in totals)
                + ". These currencies are not combined."
            )
        reply = (
            "[AGENT: BUDGET]\n\n"
            f"{prose}\n\n"
            '{"intent":"transaction_category_total","steps":["Filter transactions by date and category","Sum expense debits by currency"],'
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
    # Empty transaction history already has a deterministic fallback; avoid
    # invoking local generation when there is no transaction data to explain.
    if lines and settings.finmate_use_llm and llm_available():
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
