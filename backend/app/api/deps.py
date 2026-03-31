import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.session import get_db
from app.security.jwt_tokens import decode_token_subject

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    uid = decode_token_subject(credentials.credentials)
    if uid is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = db.get(User, uid)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account has no password; register again or reset",
        )
    return user


def get_current_user_id(current: User = Depends(get_current_user)) -> uuid.UUID:
    return current.id
