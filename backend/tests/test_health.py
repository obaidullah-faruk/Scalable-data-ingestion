from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.main import create_app


def client_with_database(database_url: str) -> TestClient:
    testing_session = sessionmaker(bind=create_engine(database_url))

    def override_session() -> Generator[Session]:
        with testing_session() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db_session] = override_session
    return TestClient(app)


@pytest.fixture
def unreachable_database() -> TestClient:
    # Nothing listens on port 1, so every connection is refused.
    return client_with_database("postgresql+psycopg://user:password@127.0.0.1:1/db")


def test_live_returns_success() -> None:
    response = TestClient(create_app()).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_success_when_database_answers() -> None:
    response = client_with_database("sqlite://").get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_fails_when_database_is_down(unreachable_database: TestClient) -> None:
    assert unreachable_database.get("/health/ready").status_code == 503


def test_live_ignores_database_outage(unreachable_database: TestClient) -> None:
    assert unreachable_database.get("/health/live").status_code == 200
