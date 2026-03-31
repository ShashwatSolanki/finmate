#!/usr/bin/env python3
"""
Load CSV rows into FinMate `transactions` for one user (Postgres).

Run from the backend folder so imports resolve:

  cd backend
  .venv\\Scripts\\python scripts/csv_seed_transactions.py --user-id YOUR-UUID --csv ../training/data/personal_finance_tracker_dataset.csv --limit 500

  .venv\\Scripts\\python scripts/csv_seed_transactions.py --user-id YOUR-UUID --csv "../training/data/Indian Personal Finance and Spending Habits.csv" --format indian --limit 200

Requires DATABASE_URL in .env (same as FastAPI). User must exist (register via /api/auth/register first).

Convention: expenses are stored as negative amounts (matches Budget Planner totals).
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.db.models import Transaction, User  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

INDIAN_CATEGORY_COLS = [
    "Rent",
    "Loan_Repayment",
    "Insurance",
    "Groceries",
    "Transport",
    "Eating_Out",
    "Entertainment",
    "Utilities",
    "Healthcare",
    "Education",
    "Miscellaneous",
]


def parse_date(raw: str | None) -> date:
    if not raw:
        return date.today()
    s = str(raw).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return date.today()


def dec(raw: str | None) -> Decimal:
    try:
        return Decimal(str(raw).strip().replace(",", ""))
    except (InvalidOperation, AttributeError):
        return Decimal("0")


def detect_format(header: list[str]) -> str:
    h = [x.strip().lower() for x in header]
    if "monthly_expense_total" in h and "category" in h:
        return "tracker"
    if "groceries" in h and "eating_out" in h and "income" in h:
        return "indian"
    return "unknown"


def seed_tracker(user_id: UUID, row: dict) -> Transaction | None:
    exp = row.get("monthly_expense_total")
    if exp is None or str(exp).strip() == "":
        return None
    amount = -abs(dec(exp))
    cat = (row.get("category") or "uncategorized").strip()[:64]
    d = parse_date(row.get("date"))
    scen = (row.get("financial_scenario") or "").strip()
    cf = (row.get("cash_flow_status") or "").strip()
    desc = ", ".join(x for x in (scen, f"cash_flow={cf}" if cf else "") if x)[:2000]
    return Transaction(
        user_id=user_id,
        amount=amount,
        currency="USD",
        category=cat or None,
        description=desc or None,
        occurred_on=d,
    )


def seed_indian(user_id: UUID, row: dict) -> list[Transaction]:
    out: list[Transaction] = []
    d = parse_date(row.get("record_date"))
    for col in INDIAN_CATEGORY_COLS:
        raw = row.get(col)
        v = dec(raw)
        if v <= 0:
            continue
        amt = -abs(v)
        cat = col.replace("_", " ")[:64]
        out.append(
            Transaction(
                user_id=user_id,
                amount=amt,
                currency="INR",
                category=cat,
                description=f"Imported from Indian spending habits CSV ({col})",
                occurred_on=d,
            )
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed transactions from CSV for one user")
    ap.add_argument("--user-id", required=True, type=UUID, help="Target user UUID (from /api/auth/register or /api/users/me)")
    ap.add_argument("--csv", required=True, help="Path to CSV file")
    ap.add_argument("--limit", type=int, default=0, help="Max data rows (0 = all)")
    ap.add_argument("--format", choices=("auto", "tracker", "indian"), default="auto")
    ap.add_argument("--dry-run", action="store_true", help="Parse only; do not commit")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.is_file():
        print(f"File not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    db = SessionLocal()
    try:
        user = db.get(User, args.user_id)
        if not user:
            print(f"User not found: {args.user_id}", file=sys.stderr)
            sys.exit(1)

        with csv_path.open(newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            header = [x.strip() for x in (reader.fieldnames or [])]
            fmt = args.format
            if fmt == "auto":
                fmt = detect_format(header)
                if fmt == "unknown":
                    print("Could not detect format; use --format tracker or indian", file=sys.stderr)
                    sys.exit(1)
                print(f"Detected format: {fmt}")

            n = 0
            total_inserts = 0
            for row in reader:
                if args.limit and n >= args.limit:
                    break
                row = {k.strip(): v for k, v in row.items() if k}
                if fmt == "tracker":
                    t = seed_tracker(args.user_id, row)
                    if t is None:
                        continue
                    if args.dry_run:
                        print(f"[dry-run] {t.occurred_on} {t.category} {t.amount}")
                    else:
                        db.add(t)
                    total_inserts += 1
                else:
                    for t in seed_indian(args.user_id, row):
                        if args.dry_run:
                            print(f"[dry-run] {t.occurred_on} {t.category} {t.amount} {t.currency}")
                        else:
                            db.add(t)
                        total_inserts += 1
                n += 1

            if not args.dry_run:
                db.commit()
                print(f"Committed {total_inserts} transaction row(s) from {n} CSV row(s) for user {args.user_id}.")
            else:
                print(f"Dry-run: would insert {total_inserts} transaction row(s) from {n} CSV row(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
