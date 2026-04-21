import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import MemoryChunk, User
from app.db.session import get_db

router = APIRouter()


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None

    model_config = {"from_attributes": True}


class OnboardingBody(BaseModel):
    monthly_income: float = Field(..., gt=0)
    location: str = Field(..., min_length=2, max_length=120)
    goals: list[str] = Field(default_factory=list, max_length=8)
    risk_tolerance: str = Field(default="moderate", max_length=32)
    currency: str = Field(default="USD", max_length=8)


class OnboardingOut(BaseModel):
    saved: bool
    profile_summary: str


@router.get("/me", response_model=UserOut)
def read_me(current: User = Depends(get_current_user)) -> User:
    """Current profile (requires Bearer token)."""
    return current


@router.post("/onboarding", response_model=OnboardingOut)
def save_onboarding(
    body: OnboardingBody,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> OnboardingOut:
    goals = [g.strip() for g in body.goals if g.strip()]
    goal_text = ", ".join(goals) if goals else "not provided"
    profile = (
        "User financial profile\n"
        f"- Monthly income: {body.monthly_income:.2f} {body.currency}\n"
        f"- Location: {body.location.strip()}\n"
        f"- Risk tolerance: {body.risk_tolerance.strip().lower()}\n"
        f"- Goals: {goal_text}"
    )
    row = MemoryChunk(user_id=current.id, content=profile, source="onboarding")
    db.add(row)
    db.commit()
    return OnboardingOut(saved=True, profile_summary=profile)


@router.get("/onboarding/latest", response_model=OnboardingOut)
def latest_onboarding(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> OnboardingOut:
    row = db.scalar(
        select(MemoryChunk)
        .where(MemoryChunk.user_id == current.id, MemoryChunk.source == "onboarding")
        .order_by(MemoryChunk.created_at.desc())
        .limit(1)
    )
    if not row:
        return OnboardingOut(saved=False, profile_summary="")
    return OnboardingOut(saved=True, profile_summary=row.content)
