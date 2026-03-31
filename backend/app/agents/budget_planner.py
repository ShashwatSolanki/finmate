"""Budget Planner agent — DB aggregates + month-over-month insights + RAG context."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.types import AgentName, AgentResult
from app.db.models import Transaction
from app.ml.finmate import generate
from app.services.spending_insights import category_delta_vs_prior_month


def run(
    user_id: UUID,
    message: str,
    db: Session,
    rag_context: str | None = None,
) -> AgentResult:
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
    for cat, total in rows:
        key = cat or "uncategorized"
        by_cat[key] = Decimal(str(total))

    total_flow = sum(by_cat.values(), start=Decimal("0"))
    top = sorted(by_cat.items(), key=lambda x: abs(x[1]), reverse=True)[:8]
    lines = [f"- {k}: {v} {currency}" for k, v in top]

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

    # Build enriched prompt for FinMate
    enriched_message = (
        f"{message}\n\n"
        f"[User financial data]\n{data_summary}{rag_block}"
    )

    reply = generate(enriched_message)

    return AgentResult(
        agent=AgentName.BUDGET_PLANNER,
        reply=reply,
        planned_steps=["load_transactions_30d", "aggregate_by_category", "mom_insights", "retrieve_rag", "finmate_generate"],
        metadata={"window_days": "30", "categories_found": str(len(by_cat))},
    )