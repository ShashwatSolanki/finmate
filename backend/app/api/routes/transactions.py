import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import Transaction, User
from app.db.session import get_db

router = APIRouter()


class TransactionCreate(BaseModel):
    amount: Decimal = Field(..., description="Negative for expense, positive for income if you use that convention")
    currency: str = "USD"
    category: str | None = None
    description: str | None = None
    occurred_on: date


class TransactionOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    amount: Decimal
    currency: str
    category: str | None
    description: str | None
    occurred_on: date

    model_config = {"from_attributes": True}


@router.post("", response_model=TransactionOut)
def create_transaction(
    body: TransactionCreate,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> Transaction:
    row = Transaction(
        user_id=current.id,
        amount=body.amount,
        currency=body.currency,
        category=body.category,
        description=body.description,
        occurred_on=body.occurred_on,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("", response_model=list[TransactionOut])
def list_transactions(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> list[Transaction]:
    rows = db.scalars(
        select(Transaction)
        .where(Transaction.user_id == current.id)
        .order_by(Transaction.occurred_on.desc())
    ).all()
    return list(rows)


class MonthlySummary(BaseModel):
    year: int
    month: int
    total_expenses: Decimal


@router.get("/summary/monthly", response_model=MonthlySummary)
def monthly_summary(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> MonthlySummary:
    total = db.scalar(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.user_id == current.id,
            func.extract("year", Transaction.occurred_on) == year,
            func.extract("month", Transaction.occurred_on) == month,
        )
    )
    return MonthlySummary(year=year, month=month, total_expenses=Decimal(str(total or 0)))
