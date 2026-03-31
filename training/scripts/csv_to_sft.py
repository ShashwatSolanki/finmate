#!/usr/bin/env python3
"""
Turn FinMate-style CSVs into messages JSONL for QLoRA SFT.

Assistant format (every sample):
  [AGENT: BUDGET|INVESTMENT|INVOICE]

  <2–4 sentences natural language with reasoning>

  {"intent":...,"steps":[...],"tools_needed":[...],"notes":"..."}

Usage:

  python scripts/csv_to_sft.py --csv data/personal_finance_tracker_dataset.csv --out data/part.jsonl --format tracker
  python scripts/csv_to_sft.py --csv data/personal_finance_tracker_dataset.csv --out data/part_inv.jsonl --format invoice
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from finmate_nl import (
    SYSTEM_FINMATE,
    TAG_BUDGET,
    TAG_INVESTMENT,
    TAG_INVOICE,
    compose_assistant,
    dec,
    nl_budget_indian,
    nl_budget_tracker,
    nl_investment_survey,
    nl_invoice_client,
    nl_macro,
)


def row_to_messages_tracker(row: dict, row_index: int) -> dict:
    date = row.get("date", "")
    cat = row.get("category", "")
    income = row.get("monthly_income", "")
    exp = row.get("monthly_expense_total", "")
    scen = row.get("financial_scenario", "")
    cf = row.get("cash_flow_status", "")
    stress = row.get("financial_stress_level", "")
    advice = row.get("financial_advice_score", "")
    inc = dec(row.get("monthly_income"))
    ex = dec(row.get("monthly_expense_total"))
    rent = dec(row.get("rent_or_mortgage"))

    if inc > 0 and rent / inc > dec("0.8") and row_index % 6 == 0:
        user = (
            f"A very large share of my income goes to housing (about {rent} vs income {income}). "
            f"I still need to cover {cat} and other bills. How do I protect savings without moving immediately?"
        )
    elif inc > 0 and ex > inc * dec("1.05"):
        user = (
            f"I am underwater this month: expenses {exp} versus income {income}. "
            f"Main category label is {cat}. What is the first lever I should pull?"
        )
    elif inc <= 0:
        user = (
            f"My recorded income is {income} (may be missing). Expenses show {exp}. "
            f"Category focus: {cat}. How should I stabilize the picture?"
        )
    else:
        user = (
            f"I track my money monthly. As of {date}, my main spending bucket is {cat}. "
            f"Monthly income is about {income}, expenses about {exp}. "
            f"Scenario: {scen}. Cash flow feels {cf}. Stress: {stress}. "
            f"Fin health score (dataset field): {advice}. What should I prioritize this month?"
        )
    body = nl_budget_tracker(row, row_index)
    steps = [
        f"Review recent transactions for {cat} and split essential vs discretionary.",
        "Set one weekly spending cap you can actually keep for 4 weeks.",
        "Automate a small savings transfer on inflow days when cash flow is positive.",
    ]
    if str(cf).lower() == "negative" or (inc > 0 and ex > inc):
        steps.insert(0, "Close the expense-versus-income gap before adding new subscriptions or EMIs.")
    payload = {
        "intent": "budget_plan",
        "steps": steps,
        "tools_needed": ["list_transactions", "set_budget"],
        "notes": f"scenario={scen}; stress={stress}; cash_flow={cf}",
    }
    assistant = compose_assistant(TAG_BUDGET, body, payload)
    return {"messages": [{"role": "system", "content": SYSTEM_FINMATE}, {"role": "user", "content": user}, {"role": "assistant", "content": assistant}]}


def row_to_messages_indian(row: dict, row_index: int) -> dict:
    income = row.get("Income", "")
    occ = row.get("Occupation", "")
    tier = row.get("City_Tier", "")
    dependents = row.get("Dependents", "0")
    rent = row.get("Rent", "0")
    loan = row.get("Loan_Repayment", "0")
    disposable = row.get("Disposable_Income", "")
    desired_sav_pct = row.get("Desired_Savings_Percentage", "")

    def _f(x) -> float:
        try:
            return float(x or 0)
        except (TypeError, ValueError):
            return 0.0

    # Find top spendable category (excluding fixed costs like rent/loan)
    spendable_cats = [
        ("Groceries", _f(row.get("Groceries", 0))),
        ("Transport", _f(row.get("Transport", 0))),
        ("Eating_Out", _f(row.get("Eating_Out", 0))),
        ("Entertainment", _f(row.get("Entertainment", 0))),
        ("Utilities", _f(row.get("Utilities", 0))),
        ("Healthcare", _f(row.get("Healthcare", 0))),
        ("Education", _f(row.get("Education", 0))),
        ("Miscellaneous", _f(row.get("Miscellaneous", 0))),
    ]
    top = sorted(spendable_cats, key=lambda x: -x[1])[:2]
    top_name = top[0][0] if top else "Groceries"
    top_val = top[0][1] if top else 0.0

    # Find highest potential savings category
    pot_cats = [
        ("Groceries", _f(row.get("Potential_Savings_Groceries", 0))),
        ("Transport", _f(row.get("Potential_Savings_Transport", 0))),
        ("Eating_Out", _f(row.get("Potential_Savings_Eating_Out", 0))),
        ("Entertainment", _f(row.get("Potential_Savings_Entertainment", 0))),
        ("Utilities", _f(row.get("Potential_Savings_Utilities", 0))),
        ("Healthcare", _f(row.get("Potential_Savings_Healthcare", 0))),
        ("Education", _f(row.get("Potential_Savings_Education", 0))),
        ("Miscellaneous", _f(row.get("Potential_Savings_Miscellaneous", 0))),
    ]
    best_saving = sorted(pot_cats, key=lambda x: -x[1])[0]
    best_saving_name = best_saving[0]
    best_saving_val = best_saving[1]

    income_f = _f(income)
    rent_f = _f(rent)
    loan_f = _f(loan)
    disposable_f = _f(disposable)
    dep_i = int(_f(dependents))

    # Build a varied user prompt based on actual data
    rent_ratio = rent_f / income_f if income_f > 0 else 0
    loan_ratio = loan_f / income_f if income_f > 0 else 0

    if rent_ratio > 0.4:
        user = (
            f"I live in {tier}, work as {occ}, and support {dep_i} dependent(s). "
            f"Income: {income}. Rent alone is {rent} which is a large chunk. "
            f"Top flexible spend: {top_name}={top_val:.0f}. Disposable income: {disposable}. "
            f"How do I protect savings when rent is this heavy?"
        )
    elif loan_ratio > 0.3:
        user = (
            f"I live in {tier}, work as {occ}. Income: {income}, loan repayment: {loan} per month. "
            f"Biggest spends after loans: {top_name}={top_val:.0f}. "
            f"I want to save {desired_sav_pct}% but EMIs are eating into that. What do I do?"
        )
    elif dep_i >= 2:
        user = (
            f"Supporting {dep_i} dependents in {tier} as a {occ}. Income: {income}. "
            f"Largest flexible bucket: {top_name}={top_val:.0f}. "
            f"Disposable income left: {disposable}. How do I balance family needs with savings?"
        )
    else:
        top_s = ", ".join(f"{n}={v:.0f}" for n, v in top)
        user = (
            f"I live in {tier}, work as {occ}. Monthly income: {income}. "
            f"Largest spending buckets: {top_s}. Disposable: {disposable}. "
            f"Target savings: {desired_sav_pct}%. Where should I cut first?"
        )

    body = nl_budget_indian(income_f, top_name, tier, occ, row_index)

    # Use potential savings data in steps
    steps = [
        f"Focus on {best_saving_name} first — potential saving of {best_saving_val:.0f} identified in your data.",
        f"Set a weekly cap on {top_name} rather than a vague monthly limit so overruns are caught early.",
        "Keep rent and loan payments stable; look for refinancing only if rates have dropped significantly.",
    ]
    if dep_i >= 1:
        steps.append(f"With {dep_i} dependent(s), build a 1-month emergency buffer before targeting long-term savings goals.")

    payload = {
        "intent": "budget_plan",
        "steps": steps,
        "tools_needed": ["list_transactions", "set_budget"],
        "notes": f"city_tier={tier}; best_saving_category={best_saving_name}({best_saving_val:.0f})",
    }
    assistant = compose_assistant(TAG_BUDGET, body, payload)
    return {"messages": [
        {"role": "system", "content": SYSTEM_FINMATE},
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant}
    ]}

def row_to_messages_investment_survey(row: dict, row_index: int) -> dict:
    mode = row.get("Mode_of_investment", "") or ""
    per_m = row.get("Investment_per_month", "")
    goal = row.get("Goal_for_investment", "") or ""
    dur = row.get("Duration_to_save(in_Years)", "") or row.get("Duration_to_save(in_Years) ", "")
    income = row.get("Annual_income", "")
    res = row.get("Resources_used", "") or ""
    user = (
        f"I am planning to invest about {per_m} per month using {mode}. "
        f"Annual income (survey): {income}. Goal: {goal}. Time horizon (years): {dur}. "
        f"I use these resources for research: {res}. How should I structure next steps?"
    )
    body = nl_investment_survey(mode, goal, dur, per_m, row_index)
    payload = {
        "intent": "investment_analysis",
        "steps": [
            "Match instrument risk to horizon and goal; avoid leverage if horizon is short.",
            "Diversify within the chosen mode; rebalance on a calendar, not emotions.",
            "Verify liquidity for emergencies before locking funds.",
        ],
        "tools_needed": ["market_quote", "list_transactions"],
        "notes": f"goal={goal}; mode={mode}",
    }
    assistant = compose_assistant(TAG_INVESTMENT, body, payload)
    return {"messages": [{"role": "system", "content": SYSTEM_FINMATE}, {"role": "user", "content": user}, {"role": "assistant", "content": assistant}]}


def row_to_messages_macro(row: dict, row_index: int) -> dict:
    d = row.get("Date", "")
    idx = row.get("Stock Index", "")
    o = row.get("Open Price", "")
    c = row.get("Close Price", "")
    inf = row.get("Inflation Rate (%)", "")
    unemp = row.get("Unemployment Rate (%)", "")
    oil = row.get("Crude Oil Price (USD per Barrel)", "")
    user = (
        f"Market snapshot {d}: {idx} opened near {o} and closed near {c}. "
        f"Inflation {inf}%, unemployment {unemp}%. Oil {oil}. "
        f"As a retail investor, what risks should I emphasize this week?"
    )
    body = nl_macro(d, idx, o, c, inf, unemp, row_index)

    try:
        inf_f = float(inf)
        unemp_f = float(unemp)
        oil_f = float(oil)
        dropped = float(o) - float(c) > 30
    except (ValueError, TypeError):
        inf_f = unemp_f = oil_f = 0.0
        dropped = False

    if inf_f > 4:
        steps = [
            "Rotate toward inflation-resistant assets (commodities, TIPS, real assets).",
            "Avoid long-duration bonds when inflation is elevated — real yield erosion is significant.",
            "Prefer companies with strong pricing power over those with thin margins.",
        ]
    elif unemp_f > 7:
        steps = [
            "Weak labor market signals soft consumer demand — reduce exposure to discretionary sectors.",
            "Broad index funds beat stock-picking in uncertain macro environments.",
            "Keep 3-6 months of expenses liquid before adding to long-term positions.",
        ]
    elif oil_f > 90:
        steps = [
            "High oil prices pressure transport and manufacturing margins — review sector exposure.",
            "Energy stocks may outperform short term but watch for demand destruction signals.",
            "Hedge fuel-sensitive positions or reduce weight if oil stays elevated beyond 60 days.",
        ]
    elif dropped:
        steps = [
            "Wait for a confirmed reversal before adding to positions — avoid catching a falling knife.",
            "Review stop-loss levels on existing positions given intraday volatility.",
            "Check if the drop is sector-specific or broad-market before reacting.",
        ]
    else:
        steps = [
            "Separate macro shocks from company-specific news before acting.",
            "Reduce position size when inflation and volatility rise together.",
            "Prefer broad, liquid exposure when the macro picture is mixed.",
        ]

    payload = {
        "intent": "investment_analysis",
        "steps": steps,
        "tools_needed": ["market_quote"],
        "notes": f"index={idx}; date={d}; inflation={inf}%; oil={oil}",
    }
    assistant = compose_assistant(TAG_INVESTMENT, body, payload)
    return {"messages": [
        {"role": "system", "content": SYSTEM_FINMATE},
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant}
    ]}

def row_to_messages_invoice(row: dict, row_index: int) -> dict:
    """Invoice-style prompt from tracker financial columns."""
    cat = (row.get("category") or "Services").strip()
    ess = dec(row.get("essential_spending"))
    disc = dec(row.get("discretionary_spending"))
    rent = dec(row.get("rent_or_mortgage"))
    inc = dec(row.get("monthly_income"))
    user = (
        f"I need a professional invoice for my client. "
        f"Work relates to {cat}. Approximate essential spend allocation {ess}, discretionary {disc}, "
        f"rent or housing {rent}, and reference income context {inc}. "
        f"Please structure line items and totals clearly."
    )
    body = nl_invoice_client(cat, ess, disc, rent, row_index)
    payload = {
        "intent": "invoice",
        "steps": [
            "List each charge with description, quantity, unit price, and line total.",
            "Add subtotal, taxes if applicable, and grand total.",
            "Export PDF and store invoice reference for audit trail.",
        ],
        "tools_needed": ["generate_invoice"],
        "notes": f"category={cat}",
    }
    assistant = compose_assistant(TAG_INVOICE, body, payload)
    return {"messages": [{"role": "system", "content": SYSTEM_FINMATE}, {"role": "user", "content": user}, {"role": "assistant", "content": assistant}]}


def detect_format(header: list[str]) -> str:
    h = [x.strip().lower() for x in header]
    if "monthly_income" in h and "category" in h:
        return "tracker"
    if "income" in h and "groceries" in h and "eating_out" in h:
        return "indian"
    if "mode_of_investment" in h and "goal_for_investment" in h:
        return "investment_survey"
    if "stock index" in h and "open price" in h and "inflation rate" in h:
        return "macro"
    return "unknown"


def main() -> None:
    ap = argparse.ArgumentParser(description="CSV → FinMate SFT JSONL")
    ap.add_argument("--csv", required=True, help="Input CSV path")
    ap.add_argument("--out", required=True, help="Output .jsonl path")
    ap.add_argument("--limit", type=int, default=0, help="Max rows (0 = all)")
    ap.add_argument(
        "--format",
        choices=("auto", "tracker", "indian", "investment_survey", "macro", "invoice"),
        default="auto",
    )
    args = ap.parse_args()

    inp = Path(args.csv)
    if not inp.is_file():
        print(f"File not found: {inp}", file=sys.stderr)
        sys.exit(1)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    with inp.open(newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        fmt = args.format
        if fmt == "auto":
            fmt = detect_format(list(header))
            if fmt == "unknown":
                print(
                    "Could not detect format; use --format tracker|indian|investment_survey|macro|invoice",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(f"Detected format: {fmt}")

        n = 0
        with out.open("w", encoding="utf-8") as w:
            for row in reader:
                if args.limit and n >= args.limit:
                    break
                row = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k}
                try:
                    if fmt == "tracker":
                        obj = row_to_messages_tracker(row, n)
                    elif fmt == "indian":
                        obj = row_to_messages_indian(row, n)
                    elif fmt == "investment_survey":
                        obj = row_to_messages_investment_survey(row, n)
                    elif fmt == "invoice":
                        obj = row_to_messages_invoice(row, n)
                    else:
                        obj = row_to_messages_macro(row, n)
                except (ValueError, TypeError, KeyError, IndexError, ZeroDivisionError):
                    continue
                w.write(json.dumps(obj, ensure_ascii=False) + "\n")
                n += 1

    print(f"Wrote {n} lines to {out}")


if __name__ == "__main__":
    main()
