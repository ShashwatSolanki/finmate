"""Generate a held-out evaluation set with balanced agent labels."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

BUDGET_TEMPLATES = [
    "My income is {income} and rent is {rent}. How should I budget monthly?",
    "I spent {food} on food and {misc} on misc last month. Help me cut costs.",
    "Salary {income}, EMI {emi}, groceries {food}. How much should I save?",
]
INVOICE_TEMPLATES = [
    "Create invoice: {amt1} for {desc1} and {amt2} for {desc2}.",
    "Need an invoice in {currency}: {amt1} {desc1}, {amt2} {desc2}.",
    "Draft invoice line items: {amt1} {desc1}; {amt2} {desc2}.",
]
INVEST_TEMPLATES = [
    "Should I buy {ticker} now with {capital} over 6 months?",
    "Compare {ticker} and {ticker2} and suggest a cautious entry.",
    "I am medium risk. Is ${ticker} attractive at current trend?",
]
DESCS = ["web design", "seo audit", "hosting", "consulting", "maintenance", "content writing"]
TICKERS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]
CURS = ["USD", "INR", "EUR"]


def build_row(kind: str, rnd: random.Random) -> dict[str, str]:
    if kind == "budget_planner":
        msg = rnd.choice(BUDGET_TEMPLATES).format(
            income=rnd.randint(30000, 120000),
            rent=rnd.randint(7000, 40000),
            food=rnd.randint(3000, 20000),
            misc=rnd.randint(2000, 18000),
            emi=rnd.randint(5000, 30000),
        )
    elif kind == "invoice_generator":
        msg = rnd.choice(INVOICE_TEMPLATES).format(
            amt1=f"{rnd.randint(50, 2000)}.{rnd.randint(0,99):02d}",
            amt2=f"{rnd.randint(25, 1500)}.{rnd.randint(0,99):02d}",
            desc1=rnd.choice(DESCS),
            desc2=rnd.choice(DESCS),
            currency=rnd.choice(CURS),
        )
    else:
        msg = rnd.choice(INVEST_TEMPLATES).format(
            ticker=rnd.choice(TICKERS),
            ticker2=rnd.choice(TICKERS),
            capital=rnd.randint(1000, 50000),
        )
    return {"message": msg, "expected_agent": kind}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="../training/data/eval_prompts_heldout_200.jsonl")
    parser.add_argument("--total", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rnd = random.Random(args.seed)
    labels = ["budget_planner", "invoice_generator", "investment_analyser"]
    per = max(1, args.total // len(labels))
    rows: list[dict[str, str]] = []
    for label in labels:
        rows.extend(build_row(label, rnd) for _ in range(per))
    while len(rows) < args.total:
        rows.append(build_row(rnd.choice(labels), rnd))
    rnd.shuffle(rows)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in rows[: args.total]:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Wrote {args.total} rows to {out}")


if __name__ == "__main__":
    main()
