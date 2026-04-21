import uuid
import csv
import io
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


class CsvImportBody(BaseModel):
    csv_text: str = Field(..., min_length=10, max_length=300000)
    amount_column: str = Field(default="amount")
    date_column: str = Field(default="occurred_on")
    category_column: str = Field(default="category")
    description_column: str = Field(default="description")
    currency_column: str = Field(default="currency")
    default_currency: str = Field(default="USD", max_length=8)
    max_rows: int = Field(default=2000, ge=1, le=20000)


class CsvImportOut(BaseModel):
    imported_count: int
    skipped_count: int
    sample_errors: list[str] = Field(default_factory=list)


@router.post("/import/csv", response_model=CsvImportOut)
def import_transactions_csv(
    body: CsvImportBody,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> CsvImportOut:
    reader = csv.DictReader(io.StringIO(body.csv_text))
    if not reader.fieldnames:
        return CsvImportOut(imported_count=0, skipped_count=0, sample_errors=["CSV header not found"])
    imported = 0
    skipped = 0
    errors: list[str] = []
    for idx, row in enumerate(reader, start=2):
        if imported >= body.max_rows:
            break
        try:
            raw_amount = (row.get(body.amount_column) or "").strip().replace(",", "")
            raw_date = (row.get(body.date_column) or "").strip()
            if not raw_amount or not raw_date:
                skipped += 1
                continue
            amount = Decimal(raw_amount)
            occurred = date.fromisoformat(raw_date)
            currency = (row.get(body.currency_column) or body.default_currency).strip() or body.default_currency
            tx = Transaction(
                user_id=current.id,
                amount=amount,
                currency=currency[:8],
                category=((row.get(body.category_column) or "").strip() or None),
                description=((row.get(body.description_column) or "").strip() or None),
                occurred_on=occurred,
            )
            db.add(tx)
            imported += 1
        except Exception as e:
            skipped += 1
            if len(errors) < 12:
                errors.append(f"line {idx}: {e}")
    db.commit()
    return CsvImportOut(imported_count=imported, skipped_count=skipped, sample_errors=errors)
