"""Rule-based spending insights (complement LLM later)."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Transaction


def category_delta_vs_prior_month(db: Session, user_id: UUID, ref: date | None = None) -> str:
    """Compare last completed calendar month spend by category vs the month before."""
    today = ref or date.today()
    # last completed month
    if today.month == 1:
        m_y, m_m = today.year - 1, 12
    else:
        m_y, m_m = today.year, today.month - 1
    if m_m == 1:
        p_y, p_m = m_y - 1, 12
    else:
        p_y, p_m = m_y, m_m - 1

    def month_totals(year: int, month: int) -> dict[str, Decimal]:
        rows = db.execute(
            select(Transaction.category, func.coalesce(func.sum(Transaction.amount), 0)).where(
                Transaction.user_id == user_id,
                func.extract("year", Transaction.occurred_on) == year,
                func.extract("month", Transaction.occurred_on) == month,
            ).group_by(Transaction.category)
        ).all()
        out: dict[str, Decimal] = {}
        for cat, total in rows:
            key = cat or "uncategorized"
            out[key] = Decimal(str(total))
        return out

    cur = month_totals(m_y, m_m)
    prev = month_totals(p_y, p_m)
    if not cur and not prev:
        return ""

    lines: list[str] = []
    all_cats = set(cur) | set(prev)
    for cat in sorted(all_cats):
        a, b = cur.get(cat, Decimal("0")), prev.get(cat, Decimal("0"))
        if b == 0 and a == 0:
            continue
        if b == 0:
            lines.append(f"{cat}: up from 0 to {a} (new activity).")
            continue
        pct = ((a - b) / abs(b)) * 100 if b != 0 else Decimal("0")
        if abs(pct) >= 5:
            direction = "up" if a > b else "down"
            lines.append(f"{cat}: {direction} about {abs(pct):.0f}% vs prior month ({b} → {a}).")

    if not lines:
        return ""
    return "Month-over-month signals:\n" + "\n".join(lines[:6])
