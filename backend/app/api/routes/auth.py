import uuid
from datetime import datetime, timedelta, timezone
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.db.models import (
    EmailVerificationToken,
    OAuthAccount,
    PasswordResetToken,
    RefreshToken,
    User,
)
from app.db.session import get_db
from app.security.jwt_tokens import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.security.passwords import (
    hash_password,
    validate_password_strength,
    verify_password,
)
from app.security.rate_limiter import auth_rate_limiter
from app.services.email_service import (
    generate_otp,
    send_password_reset_email,
    send_verification_email,
)

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
    refresh_token: str
    token_type: str = "bearer"
    user_id: uuid.UUID
    is_verified: bool = False
    requires_verification: bool = False
    verification_code_preview: str | None = None


class RefreshTokenBody(BaseModel):
    refresh_token: str


class GoogleAuthBody(BaseModel):
    credential: str = Field(..., description="Google ID Token (JWT) or test token")


class VerifyEmailBody(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=4, max_length=16)


class ResendVerificationBody(BaseModel):
    email: EmailStr


class ForgotPasswordBody(BaseModel):
    email: EmailStr


class ResetPasswordBody(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=4, max_length=16)
    new_password: str = Field(..., min_length=8, max_length=128)


class MessageOut(BaseModel):
    message: str
    success: bool = True


def _issue_tokens_for_user(
    user: User,
    db: Session,
    requires_verification: bool = False,
    verification_code_preview: str | None = None,
) -> TokenOut:
    """Generate access token and rotating refresh token, persisting the refresh token in DB."""
    access_token = create_access_token(user.id)
    refresh_token_str, jti, expires_at = create_refresh_token(user.id)

    rf_record = RefreshToken(
        user_id=user.id,
        token_jti=jti,
        expires_at=expires_at,
        revoked=False,
    )
    db.add(rf_record)
    db.commit()
    return TokenOut(
        access_token=access_token,
        refresh_token=refresh_token_str,
        user_id=user.id,
        is_verified=bool(getattr(user, "is_verified", False)),
        requires_verification=requires_verification,
        verification_code_preview=verification_code_preview,
    )


@router.post("/register", response_model=TokenOut)
def register(body: RegisterBody, db: Session = Depends(get_db)) -> TokenOut:
    # 1. Enforce password complexity policy
    is_valid_pwd, pwd_error = validate_password_strength(body.password)
    if not is_valid_pwd:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=pwd_error)

    # 2. Check duplicate email
    existing = db.query(User).filter(User.email == body.email.lower().strip()).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        email=body.email.lower().strip(),
        display_name=body.display_name,
        password_hash=hash_password(body.password),
        is_active=True,
        is_verified=False,
        auth_provider="local",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # 3. Generate verification token and send verification email
    otp = generate_otp()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.verification_code_expire_minutes)
    verification_token = EmailVerificationToken(
        user_id=user.id,
        code=otp,
        expires_at=expires_at,
        used=False,
    )
    db.add(verification_token)
    db.commit()

    send_verification_email(user.email, otp)
    code_preview = otp if settings.email_mock_mode else None

    return _issue_tokens_for_user(
        user,
        db,
        requires_verification=True,
        verification_code_preview=code_preview,
    )


@router.post("/verify-email", response_model=TokenOut)
def verify_email(body: VerifyEmailBody, db: Session = Depends(get_db)) -> TokenOut:
    user = db.query(User).filter(User.email == body.email.lower().strip()).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    now = datetime.now(timezone.utc)
    token_rec = (
        db.query(EmailVerificationToken)
        .filter(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.code == body.code.strip(),
            EmailVerificationToken.used.is_(False),
        )
        .order_by(EmailVerificationToken.created_at.desc())
        .first()
    )
    if not token_rec:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification code")

    exp = token_rec.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification code has expired")

    token_rec.used = True
    user.is_verified = True
    db.commit()
    db.refresh(user)

    return _issue_tokens_for_user(user, db)


@router.post("/resend-verification", response_model=MessageOut)
def resend_verification(body: ResendVerificationBody, db: Session = Depends(get_db)) -> MessageOut:
    user = db.query(User).filter(User.email == body.email.lower().strip()).first()
    if not user:
        return MessageOut(message="If the email is registered, a new verification code has been sent.")

    if user.is_verified:
        return MessageOut(message="Email is already verified.")

    # Invalidate previous unused verification tokens
    db.query(EmailVerificationToken).filter(
        EmailVerificationToken.user_id == user.id,
        EmailVerificationToken.used.is_(False),
    ).update({"used": True})

    otp = generate_otp()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.verification_code_expire_minutes)
    db.add(EmailVerificationToken(user_id=user.id, code=otp, expires_at=expires_at, used=False))
    db.commit()

    send_verification_email(user.email, otp)
    return MessageOut(message="Verification code sent successfully.")


@router.post("/forgot-password", response_model=MessageOut)
def forgot_password(body: ForgotPasswordBody, db: Session = Depends(get_db)) -> MessageOut:
    user = db.query(User).filter(User.email == body.email.lower().strip()).first()
    if not user:
        return MessageOut(message="If the email exists in our system, a password reset code has been sent.")

    # Invalidate old unused reset tokens
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used.is_(False),
    ).update({"used": True})

    otp = generate_otp()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.password_reset_code_expire_minutes)
    db.add(PasswordResetToken(user_id=user.id, code=otp, expires_at=expires_at, used=False))
    db.commit()

    send_password_reset_email(user.email, otp)
    return MessageOut(message="If the email exists in our system, a password reset code has been sent.")


@router.post("/reset-password", response_model=MessageOut)
def reset_password(body: ResetPasswordBody, db: Session = Depends(get_db)) -> MessageOut:
    # Enforce password strength
    is_valid_pwd, pwd_error = validate_password_strength(body.new_password)
    if not is_valid_pwd:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=pwd_error)

    user = db.query(User).filter(User.email == body.email.lower().strip()).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid email or reset code")

    now = datetime.now(timezone.utc)
    reset_rec = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.code == body.code.strip(),
            PasswordResetToken.used.is_(False),
        )
        .order_by(PasswordResetToken.created_at.desc())
        .first()
    )
    if not reset_rec:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset code")

    exp = reset_rec.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset code")

    # Update password and mark verified
    user.password_hash = hash_password(body.new_password)
    user.is_verified = True
    reset_rec.used = True

    # Revoke all active refresh tokens for user to force re-login on all devices
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id).update({"revoked": True})
    db.commit()

    return MessageOut(message="Password reset successfully. You can now log in with your new password.")


@router.post("/login", response_model=TokenOut)
def login(body: LoginBody, request: Request, db: Session = Depends(get_db)) -> TokenOut:
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"{client_ip}:{body.email.lower().strip()}"

    # Rate limiting protection against brute-force attacks
    if auth_rate_limiter.is_rate_limited(rate_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Please try again later.",
        )

    user = db.query(User).filter(User.email == body.email.lower().strip()).first()
    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        auth_rate_limiter.record_attempt(rate_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    if not getattr(user, "is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Please contact support.",
        )

    auth_rate_limiter.reset(rate_key)
    return _issue_tokens_for_user(user, db)


@router.post("/refresh", response_model=TokenOut)
def refresh_token_endpoint(body: RefreshTokenBody, db: Session = Depends(get_db)) -> TokenOut:
    payload = decode_token(body.refresh_token, expected_type="refresh")
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    jti = payload.get("jti")
    sub = payload.get("sub")
    if not jti or not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed refresh token")

    token_row = db.query(RefreshToken).filter(RefreshToken.token_jti == jti).first()
    if not token_row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token not recognized")

    if token_row.revoked:
        # Detected refresh token reuse / possible breach: revoke all tokens for this user
        db.query(RefreshToken).filter(RefreshToken.user_id == token_row.user_id).update({"revoked": True})
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Revoked token reuse detected. All sessions invalidated.",
        )

    now = datetime.now(timezone.utc)
    expires_at = token_row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired")

    user = db.get(User, uuid.UUID(sub))
    if not user or not getattr(user, "is_active", True):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive or not found")

    # Rotate refresh token: revoke current and issue a fresh pair
    token_row.revoked = True
    db.commit()

    return _issue_tokens_for_user(user, db)


@router.post("/logout", response_model=MessageOut)
def logout(body: RefreshTokenBody, db: Session = Depends(get_db)) -> MessageOut:
    payload = decode_token(body.refresh_token, expected_type="refresh")
    if payload and "jti" in payload:
        db.query(RefreshToken).filter(RefreshToken.token_jti == payload["jti"]).update({"revoked": True})
        db.commit()
    return MessageOut(message="Logged out successfully", success=True)


def _verify_google_credential(credential: str) -> dict:
    """Verify Google token via Google API tokeninfo or mock test payload for offline test suites."""
    # Test/mock support for unit and integration testing without network calls
    if credential.startswith("mock-google-token:") or credential.startswith("test-google:"):
        parts = credential.split(":")
        email = parts[1] if len(parts) > 1 else "google_user@example.com"
        google_id = parts[2] if len(parts) > 2 else "google-sub-123456"
        name = parts[3] if len(parts) > 3 else "Google Test User"
        return {"email": email.lower().strip(), "sub": google_id, "name": name, "email_verified": True}

    # Production Google ID Token verification via Google tokeninfo
    try:
        url = f"https://oauth2.googleapis.com/tokeninfo?id_token={credential}"
        response = httpx.get(url, timeout=5.0)
        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Google OAuth token",
            )
        data = response.json()
        if "email" not in data or "sub" not in data:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Google token missing required claims",
            )
        return data
    except httpx.RequestError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to contact Google OAuth servers",
        )


@router.post("/google", response_model=TokenOut)
def google_auth(body: GoogleAuthBody, db: Session = Depends(get_db)) -> TokenOut:
    google_data = _verify_google_credential(body.credential)
    google_sub = google_data["sub"]
    email = google_data["email"].lower().strip()
    name = google_data.get("name")

    # 1. Check if user exists with this Google ID
    user = db.query(User).filter(User.google_id == google_sub).first()

    # 2. Check if user exists by email (Account Linking by email)
    if not user:
        user = db.query(User).filter(User.email == email).first()
        if user:
            # Link Google account to existing user
            user.google_id = google_sub
            user.is_verified = True
            db.commit()

    # 3. If new user, create user record
    if not user:
        user = User(
            email=email,
            display_name=name,
            google_id=google_sub,
            auth_provider="google",
            is_active=True,
            is_verified=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    # 4. Ensure OAuthAccount record exists
    oauth_acc = (
        db.query(OAuthAccount)
        .filter(OAuthAccount.provider == "google", OAuthAccount.provider_user_id == google_sub)
        .first()
    )
    if not oauth_acc:
        oauth_acc = OAuthAccount(
            user_id=user.id,
            provider="google",
            provider_user_id=google_sub,
            email=email,
        )
        db.add(oauth_acc)
        db.commit()

    if not getattr(user, "is_active", True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is deactivated")

    return _issue_tokens_for_user(user, db)


@router.post("/link/google", response_model=MessageOut)
def link_google_account(
    body: GoogleAuthBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageOut:
    google_data = _verify_google_credential(body.credential)
    google_sub = google_data["sub"]
    email = google_data["email"].lower().strip()

    # Check if this Google ID is already linked to another account
    existing = db.query(User).filter(User.google_id == google_sub, User.id != current_user.id).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This Google account is already linked to another FinMate user.",
        )

    current_user.google_id = google_sub
    # Add or update OAuthAccount record
    oauth_acc = (
        db.query(OAuthAccount)
        .filter(OAuthAccount.user_id == current_user.id, OAuthAccount.provider == "google")
        .first()
    )
    if not oauth_acc:
        oauth_acc = OAuthAccount(
            user_id=current_user.id,
            provider="google",
            provider_user_id=google_sub,
            email=email,
        )
        db.add(oauth_acc)
    else:
        oauth_acc.provider_user_id = google_sub
        oauth_acc.email = email

    db.commit()
    return MessageOut(message="Google account linked successfully", success=True)


@router.post("/unlink/google", response_model=MessageOut)
def unlink_google_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageOut:
    if not current_user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot unlink Google account without a password set. Set a password first.",
        )

    current_user.google_id = None
    db.query(OAuthAccount).filter(
        OAuthAccount.user_id == current_user.id, OAuthAccount.provider == "google"
    ).delete()
    db.commit()
    return MessageOut(message="Google account unlinked successfully", success=True)

