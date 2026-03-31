import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.session import get_db
from app.security.jwt_tokens import create_access_token
from app.security.passwords import hash_password, verify_password

router = APIRouter()


class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str | None = None


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: uuid.UUID


@router.post("/register", response_model=TokenOut)
def register(body: RegisterBody, db: Session = Depends(get_db)) -> TokenOut:
    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    user = User(
        email=body.email,
        display_name=body.display_name,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(user.id)
    return TokenOut(access_token=token, user_id=user.id)


@router.post("/login", response_model=TokenOut)
def login(body: LoginBody, db: Session = Depends(get_db)) -> TokenOut:
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    token = create_access_token(user.id)
    return TokenOut(access_token=token, user_id=user.id)
