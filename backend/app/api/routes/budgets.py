import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import Budget, User
from app.db.session import get_db

router = APIRouter()


class BudgetFields(BaseModel):
    category: str = Field(..., min_length=1, max_length=64)
    limit_amount: Decimal = Field(..., gt=0, max_digits=14, decimal_places=2)
    period_start: date
    period_end: date

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("category must not be empty")
        return value

    @model_validator(mode="after")
    def validate_period(self) -> "BudgetFields":
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        return self


class BudgetCreate(BudgetFields):
    pass


class BudgetUpdate(BaseModel):
    category: str | None = Field(default=None, min_length=1, max_length=64)
    limit_amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    period_start: date | None = None
    period_end: date | None = None

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("category must not be empty")
        return value

    @model_validator(mode="after")
    def validate_period(self) -> "BudgetUpdate":
        if self.period_start is not None and self.period_end is not None and self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        return self


class BudgetOut(BudgetFields):
    id: uuid.UUID
    user_id: uuid.UUID

    model_config = {"from_attributes": True}


def _get_owned_budget(budget_id: uuid.UUID, current: User, db: Session) -> Budget:
    budget = db.scalar(select(Budget).where(Budget.id == budget_id, Budget.user_id == current.id))
    if budget is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found")
    return budget


@router.post("", response_model=BudgetOut, status_code=status.HTTP_201_CREATED)
def create_budget(
    body: BudgetCreate,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> Budget:
    budget = Budget(user_id=current.id, **body.model_dump())
    db.add(budget)
    db.commit()
    db.refresh(budget)
    return budget


@router.get("", response_model=list[BudgetOut])
def list_budgets(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> list[Budget]:
    return list(
        db.scalars(
            select(Budget)
            .where(Budget.user_id == current.id)
            .order_by(Budget.period_start.desc(), Budget.category)
        ).all()
    )


@router.get("/{budget_id}", response_model=BudgetOut)
def get_budget(
    budget_id: uuid.UUID,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> Budget:
    return _get_owned_budget(budget_id, current, db)


@router.patch("/{budget_id}", response_model=BudgetOut)
def update_budget(
    budget_id: uuid.UUID,
    body: BudgetUpdate,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> Budget:
    budget = _get_owned_budget(budget_id, current, db)
    values = body.model_dump(exclude_unset=True)
    for field, value in values.items():
        setattr(budget, field, value)
    if budget.period_end < budget.period_start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_end must be on or after period_start",
        )
    db.commit()
    db.refresh(budget)
    return budget


@router.delete("/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_budget(
    budget_id: uuid.UUID,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> None:
    budget = _get_owned_budget(budget_id, current, db)
    db.delete(budget)
    db.commit()