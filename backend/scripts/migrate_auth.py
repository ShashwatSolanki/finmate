import argparse
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from sqlalchemy import create_engine, inspect, text
from app.config import settings
from app.db.base import Base
from app.db.session import engine as default_engine, _ensure_auth_schema
from app.db.models import RefreshToken, OAuthAccount


def run_migration(db_url: str | None = None):
    target_engine = create_engine(db_url) if db_url else default_engine
    print(f"Starting auth schema migration on: {target_engine.url} ...")

    try:
        # Test connection
        with target_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        print(f"Could not connect to target database ({exc}).")
        if not db_url:
            print("PostgreSQL container may not be started. Falling back to local SQLite migration test...")
            target_engine = create_engine("sqlite:///auth_migration_test.db")
        else:
            raise

    Base.metadata.create_all(bind=target_engine)
    _ensure_auth_schema(target_engine)

    inspector = inspect(target_engine)
    tables = inspector.get_table_names()
    print("Database tables: " + ", ".join(tables))

    user_cols = [c["name"] for c in inspector.get_columns("users")]
    print("Users table columns: " + ", ".join(user_cols))

    assert "refresh_tokens" in tables, "refresh_tokens table missing"
    assert "oauth_accounts" in tables, "oauth_accounts table missing"
    assert "email_verification_tokens" in tables, "email_verification_tokens table missing"
    assert "password_reset_tokens" in tables, "password_reset_tokens table missing"
    assert "is_active" in user_cols, "is_active column missing on users"
    assert "is_verified" in user_cols, "is_verified column missing on users"
    assert "google_id" in user_cols, "google_id column missing on users"

    print("Auth migration completed successfully!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run FinMate auth schema migration")
    parser.add_argument("--url", default=None, help="Database URL to migrate (defaults to settings.database_url)")
    args = parser.parse_args()
    run_migration(args.url)


