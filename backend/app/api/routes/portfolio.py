from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import InvestmentHolding, User
from app.services.market_data import get_ticker

router = APIRouter()


class HoldingCreate(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16)
    quantity: Decimal = Field(..., gt=0)
    average_cost: Decimal = Field(..., gt=0)
    currency: str = Field(default="USD", min_length=1, max_length=8)


class HoldingResponse(BaseModel):
    id: UUID
    symbol: str
    quantity: Decimal
    average_cost: Decimal
    currency: str


class HoldingValuation(HoldingResponse):
    last_price: Decimal | None = None
    market_value: Decimal | None = None
    cost_basis: Decimal
    unrealized_profit: Decimal | None = None


@router.get("/holdings", response_model=list[HoldingResponse])
def list_holdings(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[InvestmentHolding]:
    return db.scalars(
        select(InvestmentHolding)
        .where(InvestmentHolding.user_id == current.id)
        .order_by(InvestmentHolding.symbol.asc())
    ).all()


@router.post("/holdings", response_model=HoldingResponse, status_code=201)
def create_holding(
    body: HoldingCreate,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InvestmentHolding:
    symbol = body.symbol.strip().upper()
    existing = db.scalar(
        select(InvestmentHolding).where(
            InvestmentHolding.user_id == current.id,
            InvestmentHolding.symbol == symbol,
        )
    )
    if existing:
        total_cost = existing.quantity * existing.average_cost + body.quantity * body.average_cost
        existing.quantity += body.quantity
        existing.average_cost = total_cost / existing.quantity
        existing.currency = body.currency.upper()
        db.commit()
        db.refresh(existing)
        return existing

    holding = InvestmentHolding(
        user_id=current.id,
        symbol=symbol,
        quantity=body.quantity,
        average_cost=body.average_cost,
        currency=body.currency.upper(),
    )
    db.add(holding)
    db.commit()
    db.refresh(holding)
    return holding


@router.delete("/holdings/{symbol}", status_code=204)
def delete_holding(
    symbol: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    holding = db.scalar(
        select(InvestmentHolding).where(
            InvestmentHolding.user_id == current.id,
            InvestmentHolding.symbol == symbol.strip().upper(),
        )
    )
    if holding is None:
        raise HTTPException(status_code=404, detail="Holding not found.")
    db.delete(holding)
    db.commit()


@router.get("/summary", response_model=list[HoldingValuation])
def portfolio_summary(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[HoldingValuation]:
    holdings = db.scalars(
        select(InvestmentHolding)
        .where(InvestmentHolding.user_id == current.id)
        .order_by(InvestmentHolding.symbol.asc())
    ).all()
    result: list[HoldingValuation] = []
    for holding in holdings:
        cost_basis = holding.quantity * holding.average_cost
        last_price: Decimal | None = None
        try:
            ticker = get_ticker(holding.symbol)
            info = ticker.info or {}
            raw = info.get("currentPrice") or info.get("regularMarketPrice")
            if raw is not None:
                last_price = Decimal(str(raw))
        except Exception:
            last_price = None
        market_value = holding.quantity * last_price if last_price is not None else None
        result.append(
            HoldingValuation(
                id=holding.id,
                symbol=holding.symbol,
                quantity=holding.quantity,
                average_cost=holding.average_cost,
                currency=holding.currency,
                last_price=last_price,
                market_value=market_value,
                cost_basis=cost_basis,
                unrealized_profit=market_value - cost_basis if market_value is not None else None,
            )
        )
    return result
