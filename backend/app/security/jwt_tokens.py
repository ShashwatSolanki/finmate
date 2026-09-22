import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID

from jose import JWTError, jwt

from app.config import settings


def create_access_token(user_id: UUID, extra_claims: dict | None = None) -> str:
    """Generate a short-lived access token with type claim."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode = {
        "sub": str(user_id),
        "iat": now,
        "exp": expire,
        "type": "access",
    }
    if extra_claims:
        to_encode.update(extra_claims)
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: UUID) -> tuple[str, str, datetime]:
    """Generate a long-lived rotating refresh token with unique jti.

    Returns: (token_string, jti, expires_at)
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=settings.refresh_token_expire_days)
    token_jti = str(uuid.uuid4())
    to_encode = {
        "sub": str(user_id),
        "iat": now,
        "exp": expire,
        "jti": token_jti,
        "type": "refresh",
    }
    token_str = jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token_str, token_jti, expire


def decode_token(token: str, expected_type: str | None = None) -> dict | None:
    """Safely decode and validate a JWT, optionally verifying its token type claim."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if expected_type:
            token_type = payload.get("type")
            if token_type and token_type != expected_type:
                return None
            if not token_type and expected_type != "access":
                return None
        return payload
    except (JWTError, ValueError):
        return None


def decode_token_subject(token: str, expected_type: str = "access") -> UUID | None:
    """Decode token subject (user_id) with token type verification."""
    payload = decode_token(token, expected_type=expected_type)
    if not payload:
        return None
    sub = payload.get("sub")
    if not sub:
        return None
    try:
        return UUID(sub)
    except (ValueError, TypeError):
        return None
