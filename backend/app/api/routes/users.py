import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import MemoryChunk, RefreshToken, User
from app.db.session import get_db
from app.security.passwords import (
    hash_password,
    validate_password_strength,
    verify_password,
)

router = APIRouter()


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    is_active: bool = True
    has_password: bool = True
    google_linked: bool = False
    auth_provider: str = "local"

    model_config = {"from_attributes": True}


class UpdateProfileBody(BaseModel):
    display_name: str | None = Field(None, max_length=120)


class ChangePasswordBody(BaseModel):
    current_password: str | None = None
    new_password: str = Field(..., min_length=8, max_length=128)


class MessageOut(BaseModel):
    message: str
    success: bool = True


class OnboardingBody(BaseModel):
    monthly_income: float = Field(..., gt=0)
    location: str = Field(..., min_length=2, max_length=120)
    goals: list[str] = Field(default_factory=list, max_length=8)
    risk_tolerance: str = Field(default="moderate", max_length=32)
    currency: str = Field(default="USD", max_length=8)


class OnboardingOut(BaseModel):
    saved: bool
    profile_summary: str


class OnboardingProfileOut(BaseModel):
    saved: bool
    monthly_income: float | None = None
    location: str | None = None
    goals: list[str] = Field(default_factory=list)
    risk_tolerance: str | None = None
    currency: str | None = None
    profile_summary: str = ""


def _parse_onboarding_profile(text: str) -> OnboardingProfileOut:
    if not text.strip():
        return OnboardingProfileOut(saved=False)

    income_m = re.search(r"Monthly income:\s*([\d,]+(?:\.\d+)?)\s*(\w+)?", text, re.I)
    location_m = re.search(r"Location:\s*([^\n]+)", text, re.I)
    risk_m = re.search(r"Risk tolerance:\s*([^\n]+)", text, re.I)
    goals_m = re.search(r"Goals:\s*([^\n]+)", text, re.I)

    monthly_income = None
    currency = None
    if income_m:
        monthly_income = float(income_m.group(1).replace(",", ""))
        currency = (income_m.group(2) or "").strip() or None

    goals: list[str] = []
    if goals_m:
        raw = goals_m.group(1).strip()
        if raw.lower() != "not provided":
            goals = [g.strip() for g in raw.split(",") if g.strip()]

    return OnboardingProfileOut(
        saved=True,
        monthly_income=monthly_income,
        location=location_m.group(1).strip() if location_m else None,
        goals=goals,
        risk_tolerance=risk_m.group(1).strip().lower() if risk_m else None,
        currency=currency,
        profile_summary=text.strip(),
    )


@router.get("/me", response_model=UserOut)
def read_me(current: User = Depends(get_current_user)) -> UserOut:
    """Current profile (requires Bearer token)."""
    return UserOut(
        id=current.id,
        email=current.email,
        display_name=current.display_name,
        is_active=getattr(current, "is_active", True),
        has_password=bool(current.password_hash),
        google_linked=bool(getattr(current, "google_id", None)),
        auth_provider=getattr(current, "auth_provider", "local"),
    )


@router.patch("/me", response_model=UserOut)
def update_profile(
    body: UpdateProfileBody,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserOut:
    """Update profile information like display name."""
    if body.display_name is not None:
        current.display_name = body.display_name.strip()
    db.commit()
    db.refresh(current)
    return UserOut(
        id=current.id,
        email=current.email,
        display_name=current.display_name,
        is_active=getattr(current, "is_active", True),
        has_password=bool(current.password_hash),
        google_linked=bool(getattr(current, "google_id", None)),
        auth_provider=getattr(current, "auth_provider", "local"),
    )


@router.post("/change-password", response_model=MessageOut)
def change_password(
    body: ChangePasswordBody,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageOut:
    """Change account password. If a password is set, verifies current_password."""
    if current.password_hash:
        if not body.current_password or not verify_password(body.current_password, current.password_hash):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect.",
            )

    is_valid, err = validate_password_strength(body.new_password)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=err,
        )

    current.password_hash = hash_password(body.new_password)
    db.commit()
    return MessageOut(message="Password updated successfully.", success=True)


@router.delete("/me", response_model=MessageOut)
def deactivate_account(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageOut:
    """Deactivate user account and revoke active sessions."""
    current.is_active = False
    db.query(RefreshToken).filter(RefreshToken.user_id == current.id).update({"revoked": True})
    db.commit()
    return MessageOut(message="Account deactivated successfully.", success=True)


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


@router.get("/onboarding/profile", response_model=OnboardingProfileOut)
def onboarding_profile(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> OnboardingProfileOut:
    row = db.scalar(
        select(MemoryChunk)
        .where(MemoryChunk.user_id == current.id, MemoryChunk.source == "onboarding")
        .order_by(MemoryChunk.created_at.desc())
        .limit(1)
    )
    if not row:
        return OnboardingProfileOut(saved=False)
    return _parse_onboarding_profile(row.content)
