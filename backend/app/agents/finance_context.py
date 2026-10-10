"""Parse income and currency hints from chat text and RAG/onboarding context."""

from __future__ import annotations

import re
from decimal import Decimal

_AMOUNT_TOKEN = r"(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3}|\d+)(?:\.\d+)?(?:\s*(?:k|m|thousands?|millions?|lacs?|lakhs?|crores?)\b)?"
_CURRENCY_TOKEN = r"(?:INR|USD|EUR|GBP|AUD|CAD|SGD|JPY|CHF|NZD|CNY|HKD|KRW|SEK|NOK|DKK|ZAR|AED|SAR|Rs\.?|₹|\$|€|£)"
_ONBOARDING_INCOME = re.compile(rf"monthly income:\s*({_AMOUNT_TOKEN})(?![\d,])\s*({_CURRENCY_TOKEN})?(?![A-Za-z])", re.I)
_SALARY_INCOME = re.compile(
    rf"(?:salary|income)(?:\s+is|\s+of)?\s*(?:rs\.?|inr|₹|usd|\$|€|eur)?\s*({_AMOUNT_TOKEN})(?![\d,])",
    re.I,
)


def _parse_amount(raw: str) -> Decimal:
    match = re.fullmatch(r"(?P<number>(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3}|\d+)(?:\.\d+)?)(?:\s*(?P<scale>k|m|thousands?|millions?|lacs?|lakhs?|crores?)\b)?", raw.strip(), re.I)
    if not match:
        raise ValueError("invalid income amount format")
    amount = Decimal(match.group("number").replace(",", ""))
    scale = (match.group("scale") or "").lower()
    multiplier = (
        Decimal("1000") if scale in {"k", "thousand", "thousands"} else
        Decimal("1000000") if scale in {"m", "million", "millions"} else
        Decimal("100000") if scale in {"lac", "lacs", "lakh", "lakhs"} else
        Decimal("10000000") if scale in {"crore", "crores"} else
        Decimal("1")
    )
    return amount * multiplier


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
            currency = {"RS": "INR", "RS.": "INR", "₹": "INR", "$": "USD", "€": "EUR", "£": "GBP"}.get(currency, currency)
            return _parse_amount(m.group(1)), currency

    for text in sources:
        m = _SALARY_INCOME.search(text)
        if m:
            currency = _currency_near_match(text, m)
            return _parse_amount(m.group(1)), currency

    # Do not infer income from an unlabeled currency amount: it may be an invoice,
    # expense, investment amount, or another domain-specific value.
    return None, None
