"""Conservative answers for explicit saved-profile fact lookups.

This helper only answers facts from retrieved context. It never invents a missing
profile value; unresolved memory questions receive an explicit abstention.
"""

from __future__ import annotations

import re


_PROFILE_INTENT = re.compile(
    r"\b(saved profile|my profile|record(?:ed)?|previously|"
    r"ask(?:ed)? you to remember|saved memory|remember)\b",
    re.I,
)
_CURRENCY = r"(?:INR|USD|EUR|GBP|Rs\.?|₹|\$|€|£)"


def _amount_fact(context: str, label_pattern: str) -> tuple[str, str | None] | None:
    # Handles both "monthly income: 46500 INR" and
    # "monthly after-tax income is INR 46500".
    pattern = re.compile(
        rf"\b(?:{label_pattern})\s*(?:is|:|=)?\s*"
        rf"(?:(?P<prefix>{_CURRENCY})\s*)?"
        rf"(?P<amount>\d[\d,]*(?:\.\d+)?)"
        rf"\s*(?P<suffix>{_CURRENCY})?\b",
        re.I,
    )
    match = pattern.search(context)
    if not match:
        return None
    currency = match.group("prefix") or match.group("suffix")
    if currency:
        normalized = currency.upper().replace(".", "")
        currency = "INR" if normalized in {"₹", "RS", "INR"} else (
            "USD" if normalized in {"$", "USD"} else (
                "EUR" if normalized in {"€", "EUR"} else (
                    "GBP" if normalized in {"£", "GBP"} else normalized
                )
            )
        )
    return match.group("amount").replace(",", ""), currency


def _format_amount(amount: str, currency: str | None) -> str:
    # Decimal-like text is formatted without introducing binary-float rounding.
    whole, dot, fraction = amount.partition(".")
    whole = f"{int(whole):,}"
    numeric = whole + (dot + fraction if dot else "")
    return f"{currency} {numeric}" if currency else numeric


def _abstain(label: str) -> str:
    return (
        f"I don't see a saved {label} in the profile memories available to me, "
        "so I won't guess."
    )


def answer_saved_profile_fact(message: str, context: str | None) -> str | None:
    """Answer a clearly requested saved-profile fact from retrieved memory only."""
    if not _PROFILE_INTENT.search(message):
        return None

    ctx = context or ""
    question = message.lower()

    if "emergency" in question and ("target" in question or "contribution" in question):
        found = _amount_fact(
            ctx,
            r"(?:monthly\s+)?emergency[- ]fund\s+(?:contribution\s+)?(?:target|goal)",
        )
        if found:
            amount, currency = found
            return f"Your saved monthly emergency-fund contribution target is {_format_amount(amount, currency)}."
        return _abstain("monthly emergency-fund contribution target")

    if re.search(r"\b(after[- ]tax\s+income|monthly\s+income|salary|income)\b", question):
        found = _amount_fact(ctx, r"monthly(?:\s+after[- ]tax)?\s+income|salary")
        label = "monthly after-tax income" if "after-tax" in question else "monthly income"
        if found:
            amount, currency = found
            return f"Your saved {label} is {_format_amount(amount, currency)}."
        return _abstain(label)

    if re.search(r"\bmonthly\s+rent\b|\brent amount\b", question):
        found = _amount_fact(ctx, r"monthly\s+rent|rent amount")
        if found:
            amount, currency = found
            return f"Your saved monthly rent is {_format_amount(*found)}."
        return _abstain("monthly rent amount")

    if "risk tolerance" in question:
        match = re.search(
            r"\b(?:investment\s+)?risk tolerance\s*(?:is|:|=)\s*"
            r"(conservative|moderate|medium|aggressive|low|high)\b",
            ctx,
            re.I,
        )
        if match:
            value = match.group(1).lower()
            if value == "medium":
                value = "moderate"
            elif value == "low":
                value = "conservative"
            elif value == "high":
                value = "aggressive"
            return f"Your saved investment risk tolerance is {value}."
        return _abstain("investment risk tolerance")

    if "time horizon" in question or "investment horizon" in question:
        match = re.search(
            r"\b(?:investment\s+)?time horizon\s*(?:is|:|=)\s*"
            r"(\d+\s*(?:years?|months?))\b",
            ctx,
            re.I,
        )
        if match:
            return f"Your saved investment time horizon is {match.group(1).lower()}."
        return _abstain("investment time horizon")

    if re.search(r"\bmonthly\s+savings\s+(?:goal|target)\b|\bsavings target\b", question):
        found = _amount_fact(ctx, r"monthly\s+savings\s+(?:goal|target)|savings target")
        if found:
            return f"Your saved monthly savings goal is {_format_amount(*found)}."
        return _abstain("monthly savings goal")

    return None
