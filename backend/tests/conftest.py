import sys
import uuid
from collections.abc import Generator

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, "backend")

from app.api.deps import get_current_user, get_db
from app.api.routes.budgets import router as budgets_router
from app.db.base import Base
from app.db.models import User


@pytest.fixture
def database() -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)


@pytest.fixture
def api_client(database: sessionmaker[Session]):
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(budgets_router, prefix="/api/budgets")
    user = User(id=uuid.uuid4(), email="owner@example.com", password_hash="hashed")

    with database.begin() as db:
        db.add(user)

    def override_db() -> Generator[Session, None, None]:
        with database() as db:
            yield db

    def override_user() -> User:
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    with TestClient(app) as client:
        yield client, user, database
