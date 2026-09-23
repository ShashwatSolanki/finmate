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


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_chat_message_metadata_column()
    _ensure_auth_schema()

