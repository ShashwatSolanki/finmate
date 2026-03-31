"""Natural-language + agent tag + compact JSON helpers for FinMate SFT."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any


TAG_BUDGET = "[AGENT: BUDGET]"
TAG_INVESTMENT = "[AGENT: INVESTMENT]"
TAG_INVOICE = "[AGENT: INVOICE]"


SYSTEM_FINMATE = (
    "You are FinMate, a helpful financial assistant.\n"
    "Rules for every reply:\n"
    "1) Start with exactly one line: [AGENT: BUDGET], [AGENT: INVESTMENT], or [AGENT: INVOICE].\n"
    "2) Then write 2-4 sentences in plain English: empathetic, specific, with reasoning (not only raw percentages).\n"
    "3) End with a single line of valid JSON (no code fences) with keys: intent, steps (array), tools_needed (array), notes (string, optional)."
)


def dec(raw: str | None) -> Decimal:
    try:
        return Decimal(str(raw).strip().replace(",", ""))
    except (InvalidOperation, AttributeError):
        return Decimal("0")


def _variant(idx: int, *lines: str) -> str:
    if not lines:
        return ""
    return lines[idx % len(lines)]


def compose_assistant(agent_line: str, body: str, payload: dict[str, Any]) -> str:
    js = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    body = body.strip()
    if not body:
        body = "Let me outline a practical next step based on what you shared."
    return f"{agent_line}\n\n{body}\n\n{js}"


def nl_budget_tracker(row: dict, row_index: int) -> str:
    """Natural language + edge reasoning for tracker rows."""
    inc = dec(row.get("monthly_income"))
    ex = dec(row.get("monthly_expense_total"))
    cat = (row.get("category") or "general spending").strip()
    cf = (row.get("cash_flow_status") or "").strip()
    scen = (row.get("financial_scenario") or "").strip()
    stress = (row.get("financial_stress_level") or "").strip()
    disc = dec(row.get("discretionary_spending"))
    ess = dec(row.get("essential_spending"))

    parts: list[str] = []
    if inc <= 0:
        parts.append(
            _variant(
                row_index,
                "Your income figure looks missing or zero in this record - before cutting categories, confirm what actually hits your account each month.",
                "If income is truly near zero, the priority is stabilizing inflows (billing, pay frequency, side income) before optimizing spend.",
            )
        )
    elif ex > inc:
        parts.append(
            f"Right now expenses ({ex}) run above stated income ({inc}), which usually means drawing down savings, credit, or data mismatch - worth reconciling immediately."
        )
    elif inc > 0 and ex / inc > Decimal("0.9"):
        parts.append(
            f"Most of your income is spoken for by spending - when more than about nine-tenths goes out, small surprises can derail the month."
        )
    else:
        parts.append(
            _variant(
                row_index,
                f"Your largest labeled bucket is {cat}; that is a sensible place to look first for flexible cuts without touching essentials.",
                f"Given {scen or 'your current'} conditions, it helps to treat {cat} as the lever you can adjust week to week.",
                f"Stress reads as {stress or 'unspecified'} - if that matches how you feel, pair numeric targets with one habit change so it sticks.",
            )
        )

    if disc > 0 and ess > 0 and inc > 0:
        parts.append(
            f"Discretionary spend is materially present alongside essentials - trimming discretionary first usually protects rent, utilities, and healthcare."
        )
    elif str(cf).lower() == "negative":
        parts.append(
            "Negative cash flow on paper means you are likely borrowing from tomorrow unless something changes - pause new discretionary commitments until the gap closes."
        )
    else:
        parts.append(
            "Once the big picture is stable, automate a small transfer to savings on payday so good months do not evaporate quietly."
        )

    return " ".join(parts)[:1200]


def nl_budget_indian(income_f: float, top_name: str, tier: str, occ: str, row_index: int) -> str:
    if income_f <= 0:
        return _variant(
            row_index,
            "Income looks unrealistically low - confirm your real take-home before debating category cuts.",
            "With near-zero income in the record, focus on verifying inflows before optimising spend.",
        )

    high_income = income_f > 80000
    low_income = income_f < 25000

    if high_income:
        return _variant(
            row_index,
            f"With a stronger income base, {top_name} spending in {tier} is worth reviewing not just for cuts but for value - are you getting returns proportional to what you spend there?",
            f"Higher earners in {tier} often find lifestyle inflation hiding in {top_name} - a monthly cap rather than elimination tends to work better long term.",
            f"As a {occ} with solid income, redirecting even 10% of {top_name} spend toward investments compounds meaningfully over a 5-year horizon.",
        )
    elif low_income:
        return _variant(
            row_index,
            f"On a tighter budget in {tier}, {top_name} may feel fixed but often has small flex - even a 5% trim frees room for an emergency buffer.",
            f"For a {occ} in {tier}, protecting essentials comes first; look at {top_name} only after rent, food, and transport are secured.",
            f"Low income in {tier} means every category matters - track {top_name} weekly rather than monthly so overruns are caught early.",
        )
    else:
        return _variant(
            row_index,
            f"In {tier}, {top_name} stands out - trimming there first usually feels fairer than cutting essentials like rent or medicine.",
            f"As a {occ}, your spending on {top_name} may be habitual rather than intentional - a two-week spending pause there often reveals what you actually need.",
            f"Given your tier ({tier}), small changes to {top_name} can compound because fixed costs are harder to move quickly.",
            f"For a {occ} in {tier}, setting a weekly limit on {top_name} rather than a monthly one makes overspending easier to catch before it adds up.",
        )


def nl_investment_survey(mode: str, goal: str, dur: str, per_m: str, row_index: int) -> str:
    return _variant(
        row_index,
        f"Choosing {mode or 'a mixed approach'} is reasonable if it matches how long you can stay invested - your stated goal ({goal}) should drive risk, not the other way around.",
        f"If your horizon is about {dur} years, frequent trading inside {mode or 'that channel'} can quietly erode returns; favor simplicity and diversification.",
        f"Committing around {per_m} monthly only works if it is sustainable after emergencies - build a small buffer before maxing contributions.",
    )


def nl_macro(d: str, idx: str, o: str, c: str, inf: str, unemp: str, row_index: int) -> str:
    try:
        inf_f = float(inf)
        unemp_f = float(unemp)
        open_f = float(o)
        close_f = float(c)
    except (ValueError, TypeError):
        inf_f = unemp_f = open_f = close_f = 0.0

    dropped = open_f - close_f > 30
    high_inf = inf_f > 4.0
    high_unemp = unemp_f > 7.0

    if high_inf and dropped:
        return (
            f"On {d}, {idx} fell from {o} to {c} while inflation ran at {inf}% - "
            f"that combination typically pressures growth stocks hardest. "
            f"Trimming leverage and rotating toward defensive sectors is worth considering this week."
        )
    elif high_unemp:
        return (
            f"Unemployment near {unemp}% on {d} signals weak consumer demand beneath the surface, "
            f"even if {idx} looks stable at {c}. "
            f"Broad index exposure beats stock-picking when the labor market is this uncertain."
        )
    elif dropped:
        return (
            f"A notable intraday drop in {idx} from {o} to {c} on {d} warrants caution - "
            f"wait for a confirmed reversal before adding to positions rather than catching a falling knife."
        )
    elif high_inf:
        return (
            f"With inflation at {inf}% on {d}, real returns on cash and bonds are being quietly eroded. "
            f"{idx} closing near {c} suggests the market is still digesting the impact - "
            f"inflation-resistant assets like commodities or TIPS deserve a look."
        )
    else:
        return _variant(
            row_index,
            f"On {d}, {idx} moved from roughly {o} to {c} while inflation printed near {inf}% and unemployment near {unemp}%. That mix often rewards patience: size risk smaller when macro signals conflict.",
            f"The session in {idx} on {d} is one data point - with inflation at {inf}% and unemployment at {unemp}%, your time horizon matters more than any single week's move.",
            f"Macro conditions on {d} look relatively balanced for {idx}. Stick to your allocation plan and avoid reacting to short-term noise when fundamentals are not flashing warnings.",
        )

def nl_invoice_client(cat: str, ess: Decimal, disc: Decimal, rent: Decimal, row_index: int) -> str:
    return _variant(
        row_index,
        f"I will draft a clean invoice for {cat} with itemised lines so your client sees exactly what they are paying for - clear breakdowns reduce disputes and speed up payment.",
        f"For {cat} work, splitting fixed fees from variable charges on the invoice helps clients approve faster and flags scope creep early.",
        f"A well-structured {cat} invoice with a short payment terms line (e.g. Net 15) reduces back-and-forth and sets professional expectations from the start.",
        f"I will organise this {cat} invoice so recurring charges appear at the top and one-off items are clearly separated - clients pay faster when nothing looks ambiguous.",
    )
