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
from app.db.models import MemoryChunk, Transaction
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


def _original_user_request(message: str) -> str:
    """Remove Auto-mode observations before classifying intent or extracting amounts."""
    for marker in (
        "\n\n[Verified specialist observations]",
        "\n\nPrior verified specialist observations.",
    ):
        if marker in message:
            return message.split(marker, 1)[0].strip()
    return message.strip()


def _asks_for_transaction_total(message: str) -> bool:
    """Match a direct total-spending question, not any request that mentions expenses."""
    request = message.casefold()
    return bool(
        re.search(
            r"\bhow much\b.{0,35}\b(?:spend|spent|spending|expenses?|transactions?)\b",
            request,
        )
        or re.search(
            r"\b(?:total|sum)\b.{0,35}\b(?:spend|spent|spending|expenses?)\b",
            request,
        )
        or re.search(
            r"\b(?:spend|spent|spending|expenses?)\b.{0,35}\b(?:total|sum)\b",
            request,
        )
    )


def _asks_for_spending_report(message: str) -> bool:
    """Detect financial report/audit intents and keep numeric work out of LLM fallback."""
    return bool(
        re.search(
            r"\b(?:audit|auditing|summari[sz]e|summary|compare|comparison|month[- ]over[- ]month|"
            r"calendar month|previous month|month before|by category|category totals|recorded transactions|"
            r"recorded expenses|transaction history|expense summary|spending picture|transaction count|"
            r"count of transactions|number of transactions|date range|total expenses|expense totals|"
            r"spending totals|identify duplicates|duplicate-looking|duplicates)\b",
            message,
            re.I,
        )
        or (
            re.search(r"\b(?:transactions?|expenses?)\b", message, re.I)
            and re.search(r"\b(?:report|show|calculate|list)\b", message, re.I)
            and re.search(r"\b(?:total|totals|count|categories|date range)\b", message, re.I)
        )
    )


def _explicitly_requests_budget_plan(message: str) -> bool:
    return bool(
        re.search(
            r"\b(?:propose|create|set|suggest|design|recommend)\b.{0,35}\b(?:budget|budget caps?|allocation|split)\b",
            message,
            re.I,
        )
        or re.search(r"\b50\s*/\s*30\s*/\s*20\b|\bsplit into needs, wants, and savings\b", message, re.I)
    )


def _asks_for_investable_surplus(message: str) -> bool:
    return bool(
        re.search(
            r"\b(?:investable surplus|amount available to invest|money available for investing|"
            r"available for monthly investing|how much can i invest|how much money can i invest|"
            r"afford to invest|available to invest)\b",
            message,
            re.I,
        )
        or (
            re.search(r"\bhow much\b.{0,55}\b(?:available|surplus)\b", message, re.I)
            and re.search(r"\b(?:invest|investing|investment)\b", message, re.I)
        )
    )


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

def _is_month_comparison_request(message: str) -> bool:
    text = _original_user_request(message).casefold()
    return bool(
        re.search(r"\bmonth[- ]over[- ]month\b", text)
        or (
            re.search(r"\bcompare\b", text)
            and re.search(
                r"\b(?:previous|prior|last|most recent complete|complete)\b.{0,60}\bmonth\b",
                text,
            )
            and re.search(
                r"\b(?:month before|before it|two months|preceding month|prior month)\b",
                text,
            )
        )
    )


def _expense_window(message: str, today: date) -> tuple[date, date, str]:
    """Resolve an explicit calendar-month request or default to exactly 30 dates."""
    text = message.casefold()
    if re.search(
        r"\b(?:previous|prior|last|most recent complete)\s+(?:full\s+)?calendar\s+month\b"
        r"|\bmonth before (?:it|this)\b",
        text,
    ):
        current_start = date(today.year, today.month, 1)
        end = current_start - timedelta(days=1)
        start = date(end.year, end.month, 1)
        return start, end, f"previous calendar month ({start.isoformat()} to {end.isoformat()})"
    if re.search(r"\b(?:this|current)\s+calendar\s+month\b", text):
        start = date(today.year, today.month, 1)
        return start, today, f"current calendar month ({start.isoformat()} to {today.isoformat()})"
    start = today - timedelta(days=29)
    return start, today, f"last 30 days ({start.isoformat()} to {today.isoformat()})"


def _previous_calendar_months(today: date) -> tuple[tuple[date, date, str], tuple[date, date, str]]:
    """Return (most recent complete month, month before that), both inclusive."""
    this_month_start = date(today.year, today.month, 1)
    newer_end = this_month_start - timedelta(days=1)
    newer_start = date(newer_end.year, newer_end.month, 1)
    older_end = newer_start - timedelta(days=1)
    older_start = date(older_end.year, older_end.month, 1)
    newer = (newer_start, newer_end, newer_start.strftime("%B %Y"))
    older = (older_start, older_end, older_start.strftime("%B %Y"))
    return newer, older


def _aggregate_expenses(
    db: Session,
    user_id: UUID,
    start: date,
    end: date,
) -> dict[str, dict[str, Decimal]]:
    """Return positive expense magnitudes grouped by currency and category."""
    rows = db.execute(
        select(
            Transaction.currency,
            Transaction.category,
            func.coalesce(func.sum(-Transaction.amount), 0),
        )
        .where(
            Transaction.user_id == user_id,
            Transaction.occurred_on >= start,
            Transaction.occurred_on <= end,
            Transaction.amount < 0,
        )
        .group_by(Transaction.currency, Transaction.category)
    ).all()
    result: dict[str, dict[str, Decimal]] = {}
    for cur, category, total in rows:
        currency_code = str(cur or "INR").upper()
        display = (str(category).strip() if category else "Uncategorized")
        bucket = result.setdefault(currency_code, {})
        bucket[display] = bucket.get(display, Decimal("0")) + Decimal(str(total or 0))
    return result


def _currency_total(categories: dict[str, Decimal]) -> Decimal:
    return sum(categories.values(), start=Decimal("0"))


def _latest_csv_import_summary(db: Session, user_id: UUID) -> tuple[str, int, int] | None:
    """Read the latest persisted CSV import receipt without inferring from transaction rows."""
    row = db.scalars(
        select(MemoryChunk)
        .where(
            MemoryChunk.user_id == user_id,
            MemoryChunk.source == "chat",
            MemoryChunk.content.ilike("%Imported transactions CSV file:%"),
        )
        .order_by(MemoryChunk.created_at.desc(), MemoryChunk.id.desc())
        .limit(1)
    ).first()
    content = getattr(row, "content", "") if row is not None else ""
    if not isinstance(content, str) or not content:
        return None

    filename_match = re.search(
        r"(?im)^\s*Imported transactions CSV file:\s*(.+?)\s*$",
        content,
    )
    counts_match = re.search(
        r"(?im)^\s*Rows imported:\s*(\d+)\s*;\s*rows skipped:\s*(\d+)\s*\.?\s*$",
        content,
    )
    if not filename_match or not counts_match:
        return None
    return (
        filename_match.group(1).strip(),
        int(counts_match.group(1)),
        int(counts_match.group(2)),
    )


def _asks_about_latest_csv_import(message: str) -> bool:
    return bool(
        re.search(
            r"\b(?:latest|most recent|last)\s+csv\b|\btransactions?\s+imported\s+from\s+(?:the\s+)?(?:latest|most recent|last)\s+csv\b",
            message,
            re.I,
        )
        or re.search(r"\bhow many\s+transactions?\s+(?:were\s+)?imported\b", message, re.I)
    )


def _count_transactions(db: Session, user_id: UUID, start: date, end: date) -> int:
    count = db.scalar(
        select(func.count(Transaction.id)).where(
            Transaction.user_id == user_id,
            Transaction.occurred_on >= start,
            Transaction.occurred_on <= end,
        )
    )
    try:
        return int(count or 0)
    except (TypeError, ValueError):
        return 0


def _potential_duplicate_rows(
    db: Session,
    user_id: UUID,
    start: date,
    end: date,
    *,
    limit: int = 8,
    category: str | None = None,
) -> list[str]:
    """Report exact-match transaction groups without changing or excluding records."""
    normalized_category = func.lower(func.trim(func.coalesce(Transaction.category, "")))
    normalized_description = func.lower(func.trim(func.coalesce(Transaction.description, "")))
    filters = [
        Transaction.user_id == user_id,
        Transaction.occurred_on >= start,
        Transaction.occurred_on <= end,
    ]
    if category:
        filters.append(normalized_category == category.casefold())
    rows = db.execute(
        select(
            Transaction.occurred_on,
            normalized_category,
            normalized_description,
            Transaction.amount,
            Transaction.currency,
            func.count(Transaction.id),
        )
        .where(*filters)
        .group_by(
            Transaction.occurred_on,
            normalized_category,
            normalized_description,
            Transaction.amount,
            Transaction.currency,
        )
        .having(func.count(Transaction.id) > 1)
        .order_by(func.count(Transaction.id).desc())
        .limit(limit)
    ).all()

    findings: list[str] = []
    for occurred_on, category, description, amount, currency_code, count in rows:
        code = str(currency_code or "INR").upper()
        value = Decimal(str(amount))
        kind = "expense debit" if value < 0 else "transaction"
        findings.append(
            f"- {occurred_on.isoformat()} | {category or 'uncategorized'} | "
            f"{description or 'no description'} | {_format_amount(abs(value))} {code} "
            f"({kind}) appears {int(count)} times."
        )
    return findings


def _duplicate_warning_lines(
    db: Session,
    user_id: UUID,
    start: date,
    end: date,
    *,
    category: str | None = None,
) -> list[str]:
    findings = _potential_duplicate_rows(db, user_id, start, end, category=category)
    if not findings:
        return []
    return [
        "\nPotential exact-match duplicate-looking records (not removed or excluded from totals):",
        *findings,
        "These rows match on date, category, description, amount, and currency. Confirm manually before deciding whether they are duplicates.",
    ]


def _format_expense_groups(groups: dict[str, dict[str, Decimal]]) -> tuple[list[str], dict[str, Decimal]]:
    lines: list[str] = []
    totals: dict[str, Decimal] = {}
    if not groups:
        return ["No recorded expense debits were found for this period. This does not mean your spending was zero."], totals
    for currency_code in sorted(groups):
        categories = groups[currency_code]
        total = _currency_total(categories)
        totals[currency_code] = total
        lines.append(f"Total recorded expenses in {currency_code}: {_format_amount(total)}")
        for category, amount in sorted(categories.items(), key=lambda pair: pair[1], reverse=True):
            lines.append(f"- {category}: {_format_amount(amount)} {currency_code}")
    return lines, totals


def _deterministic_spending_report(
    db: Session,
    user_id: UUID,
    message: str,
    rag_context: str | None = None,
) -> AgentResult:
    today = date.today()
    if _is_month_comparison_request(message):
        newer, older = _previous_calendar_months(today)
        newer_data = _aggregate_expenses(db, user_id, newer[0], newer[1])
        older_data = _aggregate_expenses(db, user_id, older[0], older[1])
        lines = [
            f"Comparison: {newer[2]} vs {older[2]}",
            f"Reporting dates: {newer[0].isoformat()}–{newer[1].isoformat()} vs "
            f"{older[0].isoformat()}–{older[1].isoformat()}",
        ]
        if not newer_data and not older_data:
            lines.append("There are no recorded expense debits for either month. This does not mean spending was zero.")
        lines.extend(_duplicate_warning_lines(db, user_id, newer[0], newer[1]))
        lines.extend(_duplicate_warning_lines(db, user_id, older[0], older[1]))
        for currency_code in sorted(set(newer_data) | set(older_data)):
            current = newer_data.get(currency_code, {})
            previous = older_data.get(currency_code, {})
            lines.append(f"\n{currency_code} spending:")
            for category in sorted(set(current) | set(previous)):
                now_amount = current.get(category, Decimal("0"))
                before_amount = previous.get(category, Decimal("0"))
                change = now_amount - before_amount
                if before_amount > 0:
                    pct = change / before_amount * Decimal("100")
                    pct_text = f"{pct:+.2f}%"
                elif now_amount > 0:
                    pct_text = "new spending (no percentage available because the prior month was zero)"
                else:
                    pct_text = "0.00%"
                direction = "increased" if change > 0 else "decreased" if change < 0 else "unchanged"
                lines.append(
                    f"- {category}: {newer[2]} {_format_amount(now_amount)}; "
                    f"{older[2]} {_format_amount(before_amount)}; "
                    f"{direction} by {_format_amount(abs(change))} {currency_code} ({pct_text})."
                )
            current_total = _currency_total(current)
            previous_total = _currency_total(previous)
            change = current_total - previous_total
            pct_text = f"{change / previous_total * Decimal('100'):+.2f}%" if previous_total else (
                "new spending" if current_total else "0.00%"
            )
            lines.append(
                f"- TOTAL: {_format_amount(current_total)} vs {_format_amount(previous_total)} {currency_code}; "
                f"{'increased' if change > 0 else 'decreased' if change < 0 else 'unchanged'} "
                f"by {_format_amount(abs(change))} {currency_code} ({pct_text})."
            )
        reply = (
            "[AGENT: BUDGET]\n\n"
            + "\n".join(lines)
            + "\n\nThese are recorded expense debits only. Missing transactions are not inferred."
        )
        return AgentResult(
            agent=AgentName.BUDGET_PLANNER,
            reply=reply,
            planned_steps=["resolve_two_complete_calendar_months", "aggregate_expense_debits", "calculate_changes"],
            metadata={
                "source": "deterministic_month_comparison",
                "newer_period_start": newer[0].isoformat(),
                "newer_period_end": newer[1].isoformat(),
                "older_period_start": older[0].isoformat(),
                "older_period_end": older[1].isoformat(),
                "currency_groups": str(len(set(newer_data) | set(older_data))),
            },
        )

    start, end, period_label = _expense_window(message, today)
    groups = _aggregate_expenses(db, user_id, start, end)
    lines, totals = _format_expense_groups(groups)
    lines.extend(_duplicate_warning_lines(db, user_id, start, end))
    if _asks_about_latest_csv_import(message):
        latest_import = _latest_csv_import_summary(db, user_id)
        if latest_import is None:
            lines.append(
                "\\nNo saved CSV import receipt was found, so I cannot verify the most recent file's import count. "
                "The transaction count and category totals above are based on the transaction database."
            )
        else:
            filename, imported_count, skipped_count = latest_import
            lines.append(
                f"\\nLatest saved CSV import: {filename} — {imported_count} row(s) imported, "
                f"{skipped_count} row(s) skipped."
            )
            lines.append(
                "The CSV import count is a file-import receipt, not the number of transactions in the reporting window. "
                "Exact-match duplicate-looking rows remain included in transaction totals until you review them."
            )
    if re.search(r"\b(?:transaction count|count of transactions|number of transactions|how many transactions)\b", message, re.I):
        lines.append(f"\nRecorded transaction count: {_count_transactions(db, user_id, start, end)}")
    income, income_currency = extract_monthly_income(message, rag_context)
    wants_surplus = _asks_for_investable_surplus(message)
    wants_budget_comparison = _explicitly_requests_budget_plan(message) or wants_surplus
    if wants_budget_comparison:
        if income is None:
            lines.append("\nMonthly income was not found in the request or saved context, so I cannot calculate a remainder.")
        elif not totals and wants_surplus:
            lines.append(f"\nSaved/stated monthly income: {_format_amount(income)} {income_currency or 'INR'}.")
            lines.append(
                "\nI cannot estimate investable surplus because no recorded expense debits were found for this period. "
                "The missing expenses cannot safely be treated as zero; import or add them first."
            )
        elif not totals and _explicitly_requests_budget_plan(message):
            lines.append(
                "\nThere is no recorded expense data for this period, so the proposed budget is an income-based "
                "starting point rather than a comparison with actual spending."
            )
            lines.append(
                f"Illustrative 50/30/20 split: needs {_format_amount(income * Decimal('0.5'))} "
                f"{income_currency or 'INR'}, wants {_format_amount(income * Decimal('0.3'))}, "
                f"savings/debt payoff {_format_amount(income * Decimal('0.2'))}."
            )
        elif len(totals) == 1 and income_currency and next(iter(totals)) != income_currency:
            lines.append(
                f"\nMonthly income is recorded as {income_currency}, but expenses are recorded as "
                f"{next(iter(totals))}; I cannot subtract amounts in different currencies without an exchange rate."
            )
        elif len(totals) > 1:
            lines.append("\nExpenses are recorded in multiple currencies; no combined income remainder is calculated.")
        elif totals:
            currency_code, total = next(iter(totals.items()))
            remainder = income - total
            lines.append(
                f"\nStated monthly income: {_format_amount(income)} {income_currency or currency_code}."
            )
            lines.append(
                f"Simple remainder after recorded expenses for {period_label}: {_format_amount(remainder)} "
                f"{currency_code}."
            )
            lines.append(
                "This is not guaranteed investable surplus: transaction history may be incomplete and "
                "unrecorded expenses are not assumed to be zero."
            )
        return AgentResult(
            agent=AgentName.BUDGET_PLANNER,
            reply="[AGENT: BUDGET]\n\n" + "\n".join(lines),
            planned_steps=["aggregate_recorded_expense_debits", "compare_with_stated_income"],
            metadata={
                "source": "deterministic_budget_comparison",
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
                "currency_groups": str(len(totals)),
            },
        )

    reply = (
        "[AGENT: BUDGET]\n\n"
        f"Recorded spending report for {period_label}:\n"
        + "\n".join(lines)
        + "\n\nUploaded receipts are separate from recorded transactions unless explicitly imported as transactions."
    )
    return AgentResult(
        agent=AgentName.BUDGET_PLANNER,
        reply=reply,
        planned_steps=["resolve_reporting_window", "aggregate_expense_debits", "report_by_category"],
        metadata={
            "source": "deterministic_spending_report",
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "currency_groups": str(len(totals)),
        },
    )


def run(
    user_id: UUID,
    message: str,
    db: Session,
    rag_context: str | None = None,
) -> AgentResult:
    # Auto mode appends specialist observations to the message. Never parse
    # money, dates, or intent from observations as if they were user input.
    request = _original_user_request(message)

    # Straight arithmetic supplied explicitly in the request is deterministic.
    balance = _extract_balance_remainder(request)
    if balance is not None:
        remaining, income, expenses = balance
        currency_match = re.search(r"\b(INR|USD|EUR|GBP)\b|[₹$€£]", request, re.I)
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
    start = today - timedelta(days=29)

    # Transaction summaries and month comparisons are deterministic SQL tasks.
    # Do not let LLM prose rewrite numeric results, signs, or calendar periods.
    if _is_month_comparison_request(request):
        return _deterministic_spending_report(db, user_id, request, rag_context)
    asks_for_data_report = _asks_for_spending_report(request)
    asks_for_budget_or_surplus = (
        (_explicitly_requests_budget_plan(request) or _asks_for_investable_surplus(request))
        and (
            asks_for_data_report
            or bool(
                re.search(
                    r"\b(?:recorded expenses|actual spending|transaction history|recorded transactions|expenses)\b",
                    request,
                    re.I,
                )
            )
        )
    )
    if asks_for_budget_or_surplus:
        return _deterministic_spending_report(db, user_id, request, rag_context)
    if asks_for_data_report:
        return _deterministic_spending_report(db, user_id, request, rag_context)

    currency = (
        db.scalar(
            select(Transaction.currency).where(Transaction.user_id == user_id).limit(1)
        )
        or "USD"
    )

    rows = db.execute(
        select(
            Transaction.category,
            func.coalesce(func.sum(-Transaction.amount), 0),
        )
        .where(
            Transaction.user_id == user_id,
            Transaction.occurred_on >= start,
            Transaction.occurred_on <= today,
            Transaction.amount < 0,
            Transaction.currency == currency,
        )
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
    requested_category = _requested_transaction_category(request, category_catalog)
    if (
        requested_category is None
        and _asks_for_transaction_total(request)
        and re.search(r"\b(?:on|for)\s+[a-z]", request, re.I)
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
                Transaction.occurred_on <= today,
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
        duplicate_lines = _duplicate_warning_lines(
            db, user_id, start, today, category=requested_category
        )
        if duplicate_lines:
            prose += "\n\n" + "\n".join(duplicate_lines)
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

    income, income_currency = extract_monthly_income(request, rag_context)
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
            if _asks_for_investable_surplus(message):
                if income is not None:
                    income_note = f" Your saved monthly income is {income:,.2f} {budget_currency},"
                else:
                    income_note = ""
                return (
                    "[AGENT: BUDGET]\n\n"
                    "I can't reliably estimate your investable surplus because there is no recorded expense "
                    "history for the period in question."
                    f"{income_note} but unrecorded expenses cannot safely be treated as zero. "
                    "Import your actual expenses or add missing amounts before deciding how much is available "
                    "to invest.\n\n"
                    '{"intent":"investable_surplus_unavailable","steps":["Retrieve recorded expenses","Calculate verified cash flow","Estimate investable surplus only when inputs exist"],'
                    '"tools_needed":["list_transactions"],"notes":"insufficient recorded expenses; missing costs are not assumed to be zero"}'
                )
            if _asks_for_spending_report(message) and not _explicitly_requests_budget_plan(message):
                detail = (
                    "I don't have enough recorded transaction data to produce the requested category totals "
                    "or compare the requested calendar months. This does not mean your spending was zero. "
                    "Import your transactions from Settings, then rerun the report."
                )
                return (
                    "[AGENT: BUDGET]\n\n"
                    f"{detail}\n\n"
                    '{"intent":"spending_report_unavailable","steps":["Check transaction history","Import missing transactions","Recalculate the requested period"],'
                    '"tools_needed":["list_transactions"],"notes":"insufficient recorded transactions for requested report"}'
                )
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
