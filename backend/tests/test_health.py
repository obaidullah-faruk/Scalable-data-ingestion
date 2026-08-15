from fastapi.testclient import TestClient

from app.main import create_app


def test_health_returns_success() -> None:
    response = TestClient(create_app()).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
