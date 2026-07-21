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


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_chat_message_metadata_column()
