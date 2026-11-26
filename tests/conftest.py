import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost/gundem_test"
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("PANEL_USER", "panel")
    monkeypatch.setenv("PANEL_PASSWORD", "test-password")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    for name in ("ANTHROPIC_MODEL", "ANTHROPIC_FAST_MODEL"):
        monkeypatch.delenv(name, raising=False)
    from gundem.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def db():
    from gundem.models import Base

    engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
        session.rollback()
    Base.metadata.drop_all(engine)
    engine.dispose()
