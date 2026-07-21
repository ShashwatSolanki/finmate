"""Parse income and currency hints from chat text and RAG/onboarding context."""

from __future__ import annotations

import re
from decimal import Decimal

_ONBOARDING_INCOME = re.compile(r"monthly income:\s*([\d,]+(?:\.\d+)?)\s*(\w+)?", re.I)
_SALARY_INCOME = re.compile(
    r"(?:salary|income)(?:\s+is|\s+of)?\s*(?:rs\.?|inr|₹|usd|\$|€|eur)?\s*([\d,]+(?:\.\d+)?)",
    re.I,
)
_CURRENCY_PREFIX = re.compile(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)", re.I)


def _parse_amount(raw: str) -> Decimal:
    return Decimal(raw.replace(",", ""))


def _currency_near_match(text: str, match: re.Match[str]) -> str | None:
    window = text[max(0, match.start() - 12) : match.end() + 8].lower()
    if re.search(r"\b(rs\.?|inr|₹)\b", window):
        return "INR"
    if re.search(r"\b(usd|\$)\b", window):
        return "USD"
    if re.search(r"\b(eur|€)\b", window):
        return "EUR"
    return None


def extract_monthly_income(message: str, rag_context: str | None = None) -> tuple[Decimal | None, str | None]:
    """Return (monthly income, currency code) from the user message and optional RAG context."""
    sources = [message]
    if rag_context and rag_context.strip():
        sources.append(rag_context)

    for text in sources:
        m = _ONBOARDING_INCOME.search(text)
        if m:
            currency = (m.group(2) or "").strip().upper() or None
            return _parse_amount(m.group(1)), currency

    for text in sources:
        m = _SALARY_INCOME.search(text)
        if m:
            currency = _currency_near_match(text, m)
            return _parse_amount(m.group(1)), currency

    for text in sources:
        m = _CURRENCY_PREFIX.search(text)
        if m:
            return _parse_amount(m.group(1)), "INR"

    return None, None
