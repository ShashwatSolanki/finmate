import hashlib
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
from app.security.otp import hash_otp, verify_otp
from app.security.rate_limiter import auth_rate_limiter
from app.services.email_service import (
    generate_otp,
    send_password_reset_email,
    send_verification_email,
)

router = APIRouter()


def _auth_rate_key(request: Request, action: str, email: str) -> str:
    account_hash = hashlib.sha256(email.lower().strip().encode("utf-8")).hexdigest()
    return f"{action}:account:{account_hash}"


def _auth_ip_key(request: Request, action: str) -> str:
    client_ip = request.client.host if request.client else "unknown"
    ip_hash = hashlib.sha256(client_ip.encode("utf-8")).hexdigest()
    return f"{action}:ip:{ip_hash}"


def _check_rate_limit(key: str, ip_key: str | None = None) -> None:
    if auth_rate_limiter.is_rate_limited(key) or (ip_key and auth_rate_limiter.is_rate_limited(ip_key)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please try again later.",
        )


def _record_auth_attempt(key: str, ip_key: str | None = None) -> None:
    auth_rate_limiter.record_attempt(key)
    if ip_key:
        auth_rate_limiter.record_attempt(ip_key)


def _reset_auth_attempt(key: str, ip_key: str | None = None) -> None:
    auth_rate_limiter.reset(key)
    if ip_key:
        auth_rate_limiter.reset(ip_key)


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
    credential: str = Field(..., description="Google ID token")


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
    # Return mock codes only in non-production local/test environments.
    verification_code_preview: str | None = None


def _mock_code_preview(code: str) -> str | None:
    environment = settings.app_env.lower()
    if settings.email_mock_mode and environment not in {"production", "prod", "staging"}:
        return code
    return None


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
        code=None,
        code_hash=hash_otp(otp),
        expires_at=expires_at,
        used=False,
    )
    db.add(verification_token)
    db.commit()

    sent = send_verification_email(user.email, otp)
    if not sent and not settings.email_mock_mode:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to send verification email. Please try again later.",
        )
    code_preview = otp if settings.email_mock_mode and settings.app_env.lower() != "production" else None

    return _issue_tokens_for_user(
        user,
        db,
        requires_verification=True,
        verification_code_preview=code_preview,
    )


@router.post("/verify-email", response_model=TokenOut)
def verify_email(body: VerifyEmailBody, request: Request, db: Session = Depends(get_db)) -> TokenOut:
    rate_key = _auth_rate_key(request, "verify-email", body.email)
    ip_rate_key = _auth_ip_key(request, "verify-email")
    _check_rate_limit(rate_key, ip_rate_key)
    user = db.query(User).filter(User.email == body.email.lower().strip()).first()
    if not user:
        _record_auth_attempt(rate_key, ip_rate_key)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification code")

    if user.is_verified:
        _record_auth_attempt(rate_key, ip_rate_key)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification code")

    now = datetime.now(timezone.utc)
    candidates = (
        db.query(EmailVerificationToken)
        .filter(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.used.is_(False),
        )
        .order_by(EmailVerificationToken.created_at.desc())
        .limit(10)
        .all()
    )
    token_rec = next(
        (candidate for candidate in candidates if candidate.code_hash and verify_otp(body.code.strip(), candidate.code_hash)),
        None,
    )
    if not token_rec:
        _record_auth_attempt(rate_key, ip_rate_key)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification code")

    exp = token_rec.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now:
        _record_auth_attempt(rate_key, ip_rate_key)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification code has expired")

    _reset_auth_attempt(rate_key, ip_rate_key)
    token_rec.used = True
    user.is_verified = True
    db.commit()
    db.refresh(user)

    return _issue_tokens_for_user(user, db)


@router.post("/resend-verification", response_model=MessageOut)
def resend_verification(body: ResendVerificationBody, request: Request, db: Session = Depends(get_db)) -> MessageOut:
    rate_key = _auth_rate_key(request, "resend-verification", body.email)
    ip_rate_key = _auth_ip_key(request, "resend-verification")
    _check_rate_limit(rate_key, ip_rate_key)
    _record_auth_attempt(rate_key, ip_rate_key)
    user = db.query(User).filter(User.email == body.email.lower().strip()).first()
    if not user:
        return MessageOut(message="If the email is registered, a new verification code has been sent.")

    if user.is_verified:
        return MessageOut(message="If the email is registered and unverified, a verification code has been sent.")

    # Invalidate previous unused verification tokens
    db.query(EmailVerificationToken).filter(
        EmailVerificationToken.user_id == user.id,
        EmailVerificationToken.used.is_(False),
    ).update({"used": True})

    otp = generate_otp()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.verification_code_expire_minutes)
    db.add(EmailVerificationToken(user_id=user.id, code=None, code_hash=hash_otp(otp), expires_at=expires_at, used=False))
    db.commit()

    send_verification_email(user.email, otp)
    return MessageOut(
        message="If the email is registered and unverified, a verification code has been sent.",
        verification_code_preview=_mock_code_preview(otp),
    )


@router.post("/forgot-password", response_model=MessageOut)
def forgot_password(body: ForgotPasswordBody, request: Request, db: Session = Depends(get_db)) -> MessageOut:
    rate_key = _auth_rate_key(request, "forgot-password", body.email)
    ip_rate_key = _auth_ip_key(request, "forgot-password")
    _check_rate_limit(rate_key, ip_rate_key)
    _record_auth_attempt(rate_key, ip_rate_key)
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
    db.add(PasswordResetToken(user_id=user.id, code=None, code_hash=hash_otp(otp), expires_at=expires_at, used=False))
    db.commit()

    send_password_reset_email(user.email, otp)
    return MessageOut(
        message="If the email exists in our system, a password reset code has been sent.",
        verification_code_preview=_mock_code_preview(otp),
    )


@router.post("/reset-password", response_model=MessageOut)
def reset_password(body: ResetPasswordBody, request: Request, db: Session = Depends(get_db)) -> MessageOut:
    rate_key = _auth_rate_key(request, "reset-password", body.email)
    ip_rate_key = _auth_ip_key(request, "reset-password")
    _check_rate_limit(rate_key, ip_rate_key)
    user = db.query(User).filter(User.email == body.email.lower().strip()).first()
    if not user:
        _record_auth_attempt(rate_key, ip_rate_key)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid email or reset code")

    now = datetime.now(timezone.utc)
    candidates = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used.is_(False),
        )
        .order_by(PasswordResetToken.created_at.desc())
        .limit(10)
        .all()
    )
    reset_rec = next(
        (candidate for candidate in candidates if candidate.code_hash and verify_otp(body.code.strip(), candidate.code_hash)),
        None,
    )
    if not reset_rec:
        _record_auth_attempt(rate_key, ip_rate_key)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset code")

    exp = reset_rec.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now:
        _record_auth_attempt(rate_key, ip_rate_key)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset code")

    # Do not clear the rate-limit window until the new password is valid as well.
    is_valid_pwd, pwd_error = validate_password_strength(body.new_password)
    if not is_valid_pwd:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=pwd_error)

    _reset_auth_attempt(rate_key, ip_rate_key)

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
    rate_key = f"login:account:{hashlib.sha256(body.email.lower().strip().encode('utf-8')).hexdigest()}"
    ip_rate_key = f"login:ip:{hashlib.sha256(client_ip.encode('utf-8')).hexdigest()}"

    # Rate limiting protection against brute-force attacks
    if auth_rate_limiter.is_rate_limited(rate_key) or auth_rate_limiter.is_rate_limited(ip_rate_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Please try again later.",
        )

    user = db.query(User).filter(User.email == body.email.lower().strip()).first()
    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        _record_auth_attempt(rate_key, ip_rate_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    if not getattr(user, "is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Please contact support.",
        )
    if getattr(user, "auth_provider", "local") == "local" and not getattr(user, "is_verified", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email verification required before signing in.",
        )

    _reset_auth_attempt(rate_key, ip_rate_key)
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

    # Serialize refresh-token rotation so concurrent requests cannot both mint a new session.
    token_row = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_jti == jti)
        .with_for_update()
        .first()
    )
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
    """Verify a Google ID token and its intended audience before trusting claims."""
    if settings.app_env.lower() == "test" and settings.auth_allow_mock_google:
        if credential.startswith(("mock-google-token:", "test-google:")):
            parts = credential.split(":")
            if len(parts) < 3 or "@" not in parts[1] or not parts[2].strip():
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid test Google token")
            return {
                "email": parts[1].lower().strip(),
                "sub": parts[2].strip(),
                "name": parts[3] if len(parts) > 3 else "Google Test User",
                "email_verified": True,
                "aud": settings.google_client_id,
                "iss": "https://accounts.google.com",
            }

    if not settings.google_client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured",
        )
    try:
        response = httpx.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": credential},
            timeout=5.0,
        )
        if response.status_code != 200:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google OAuth token")
        data = response.json()
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to contact Google OAuth servers",
        ) from exc
    issuer = data.get("iss")
    email_verified = data.get("email_verified") in (True, "true", "True", "1", 1)
    if (
        not data.get("email")
        or not data.get("sub")
        or data.get("aud") != settings.google_client_id
        or issuer not in ("accounts.google.com", "https://accounts.google.com")
        or not email_verified
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google token claims are invalid for this application",
        )
    return data


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

