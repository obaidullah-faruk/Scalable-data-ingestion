import pytest

from app.core.config import get_settings
from app.integrations.s3 import get_s3_client, get_s3_presign_client


@pytest.fixture(autouse=True)
def clear_caches():
    for cached in (get_settings, get_s3_client, get_s3_presign_client):
        cached.cache_clear()
    yield
    for cached in (get_settings, get_s3_client, get_s3_presign_client):
        cached.cache_clear()


def test_clients_use_aws_when_no_endpoint_is_set(monkeypatch) -> None:
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("S3_BROWSER_ENDPOINT_URL", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")

    assert get_s3_client().meta.endpoint_url == "https://s3.eu-west-1.amazonaws.com"
    assert get_s3_presign_client().meta.endpoint_url == "https://s3.eu-west-1.amazonaws.com"


def test_presign_client_uses_the_browser_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://floci:4566")
    monkeypatch.setenv("S3_BROWSER_ENDPOINT_URL", "http://s3.example.test")

    assert get_s3_client().meta.endpoint_url == "http://floci:4566"
    assert get_s3_presign_client().meta.endpoint_url == "http://s3.example.test"
