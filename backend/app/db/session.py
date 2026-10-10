from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.base import Base

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_chat_message_metadata_column() -> None:
    inspector = inspect(engine)
    if "chat_messages" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("chat_messages")}
    if "metadata" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE chat_messages ADD COLUMN metadata JSONB NULL"))


def _ensure_auth_schema(target_engine=None) -> None:
    eng = target_engine or engine
    inspector = inspect(eng)
    table_names = inspector.get_table_names()
    if "users" not in table_names:
        return

    user_cols = {col["name"] for col in inspector.get_columns("users")}
    with eng.begin() as conn:
        if "is_active" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT TRUE"))
        if "is_verified" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_verified BOOLEAN DEFAULT FALSE"))
        if "google_id" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN google_id VARCHAR(128) NULL"))
        if "auth_provider" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN auth_provider VARCHAR(32) DEFAULT 'local'"))


def _ensure_otp_hash_schema(target_engine=None) -> None:
    """Upgrade legacy OTP tables and invalidate plaintext codes during the one-time migration."""
    eng = target_engine or engine
    if eng.dialect.name != "postgresql":
        # Fresh SQLite test databases receive the current schema from Base.metadata.create_all.
        return
    inspector = inspect(eng)
    tables = set(inspector.get_table_names())
    with eng.begin() as conn:
        for table in ("email_verification_tokens", "password_reset_tokens"):
            if table not in tables:
                continue
            columns = {col["name"] for col in inspect(eng).get_columns(table)}
            if "code_hash" in columns:
                continue
            # Existing codes cannot be migrated to salted hashes in SQL. Expire them
            # safely so users must request a fresh code after deployment.
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN code_hash VARCHAR(128) NULL"))
            conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN code DROP NOT NULL"))
            conn.execute(text(f"UPDATE {table} SET code = NULL WHERE code IS NOT NULL"))


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_chat_message_metadata_column()
    _ensure_auth_schema()
    _ensure_otp_hash_schema()

