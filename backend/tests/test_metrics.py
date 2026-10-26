from fastapi.testclient import TestClient

from app.main import create_app


def test_metrics_expose_request_counts_without_health_checks() -> None:
    client = TestClient(create_app())
    client.get("/health/live")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "# TYPE http_requests_total counter" in response.text
    assert "/health/live" not in response.text
