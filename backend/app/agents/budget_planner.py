"""Budget Planner agent — DB aggregates + month-over-month insights + RAG context."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.finance_context import extract_monthly_income
from app.agents.types import AgentName, AgentResult
from app.config import settings
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

    income, income_currency = extract_monthly_income(message, rag_context)
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

    if settings.finmate_use_llm:
        enriched_message = (
            f"{message}\n\n"
            f"[User financial data]\n{data_summary}{rag_block}"
        )
        try:
            reply = generate(enriched_message)
        except Exception:
            reply = _db_reply()
    else:
        reply = _db_reply()

    agent_meta: dict[str, str] = {
        "window_days": "30",
        "categories_found": str(len(by_cat)),
    }
    if income is not None:
        agent_meta["income_detected"] = f"{income:,.2f} {budget_currency}"

    return AgentResult(
        agent=AgentName.BUDGET_PLANNER,
        reply=reply,
        planned_steps=["load_transactions_30d", "aggregate_by_category", "mom_insights", "retrieve_rag", "finmate_generate"],
        metadata=agent_meta,
    )