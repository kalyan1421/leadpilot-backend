"""Shared pytest fixtures — an isolated in-memory SQLite DB per test, so tests
never touch the real dev/prod Postgres database and leave nothing behind."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture()
def client(db_session):
    """Deliberately NOT `with TestClient(app) as c: ...` — that form runs the
    app's lifespan, and app.main's startup handler calls recover_stuck_jobs(),
    which opens its own SessionLocal() straight to the REAL configured
    database, bypassing the get_db override below entirely. Instantiating
    without the context manager skips lifespan (confirmed: requests still
    work, startup/shutdown just never fire), keeping tests fully isolated to
    the in-memory SQLite session."""

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
