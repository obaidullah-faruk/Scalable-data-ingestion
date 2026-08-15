import uuid
from collections.abc import Generator
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.session import get_db_session
from app.integrations.s3 import get_s3_client, get_s3_presign_client
from app.main import create_app
from app.models import Base, IngestionRun, RunStatus


class FakeS3Client:
    def __init__(self) -> None:
        self.uploads: list[dict] = []
        self.aborts: list[dict] = []

    def create_multipart_upload(self, **arguments: object) -> dict[str, str]:
        self.uploads.append(arguments)
        return {"UploadId": f"upload-{len(self.uploads)}"}

    def generate_presigned_url(
        self,
        operation: str,
        *,
        Params: dict[str, object],
        ExpiresIn: int,
    ) -> str:
        query = urlencode(
            {
                "uploadId": Params["UploadId"],
                "partNumber": Params["PartNumber"],
                "expires": ExpiresIn,
            }
        )
        return (
            f"http://localhost:4566/{Params['Bucket']}/{Params['Key']}?{query}"
        )

    def abort_multipart_upload(self, **arguments: object) -> None:
        self.aborts.append(arguments)


@pytest.fixture
def api_context() -> Generator[tuple[TestClient, FakeS3Client, sessionmaker], None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    fake_s3 = FakeS3Client()
    settings = Settings(
        _env_file=None,
        database_url="sqlite://",
        upload_part_size_bytes=8 * 1024 * 1024,
        max_upload_size_bytes=5 * 1024 * 1024 * 1024,
        part_url_batch_limit=100,
        presigned_url_expiration_seconds=900,
    )

    def override_session() -> Generator[Session, None, None]:
        with testing_session() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_s3_client] = lambda: fake_s3
    app.dependency_overrides[get_s3_presign_client] = lambda: fake_s3
    app.dependency_overrides[get_settings] = lambda: settings

    with TestClient(app) as client:
        yield client, fake_s3, testing_session


def create_run(client: TestClient, byte_size: int = 20_000_000):
    return client.post(
        "/api/v1/ingestion-runs",
        json={
            "filename": "gdp.csv",
            "content_type": "text/csv",
            "byte_size": byte_size,
        },
    )


def test_same_filename_creates_independent_runs(api_context) -> None:
    client, fake_s3, testing_session = api_context

    first = create_run(client)
    second = create_run(client)

    assert first.status_code == 201
    assert second.status_code == 201
    first_body = first.json()
    second_body = second.json()
    assert first_body["run_id"] != second_body["run_id"]
    assert first_body["upload_id"] != second_body["upload_id"]
    assert first_body["object_key"] != second_body["object_key"]
    assert first_body["object_key"] == f"uploads/{first_body['run_id']}/source.csv"
    assert second_body["object_key"] == f"uploads/{second_body['run_id']}/source.csv"
    assert first_body["total_parts"] == 3
    assert len(fake_s3.uploads) == 2

    with testing_session() as session:
        runs = session.scalars(select(IngestionRun)).all()
        assert len(runs) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"filename": "gdp.txt", "content_type": "text/csv", "byte_size": 1},
        {"filename": "../gdp.csv", "content_type": "text/csv", "byte_size": 1},
        {"filename": "gdp.csv", "content_type": "application/json", "byte_size": 1},
        {"filename": "gdp.csv", "content_type": "text/csv", "byte_size": 0},
        {
            "filename": "gdp.csv",
            "content_type": "text/csv",
            "byte_size": 5 * 1024 * 1024 * 1024 + 1,
        },
    ],
)
def test_create_run_rejects_invalid_file_metadata(api_context, payload) -> None:
    client, fake_s3, _ = api_context

    response = client.post("/api/v1/ingestion-runs", json=payload)

    assert response.status_code == 422
    assert fake_s3.uploads == []


def test_part_url_request_is_bounded_to_required_parts(api_context) -> None:
    client, _, _ = api_context
    run = create_run(client).json()

    response = client.post(
        run["part_urls_endpoint"],
        json={"part_numbers": [3, 1]},
    )
    invalid = client.post(
        run["part_urls_endpoint"],
        json={"part_numbers": [4]},
    )

    assert response.status_code == 200
    assert [part["part_number"] for part in response.json()["parts"]] == [1, 3]
    assert all(
        part["url"].startswith("http://localhost:4566/")
        for part in response.json()["parts"]
    )
    assert invalid.status_code == 422


def test_abort_is_idempotent_and_marks_run_failed(api_context) -> None:
    client, fake_s3, testing_session = api_context
    run_id = create_run(client).json()["run_id"]

    first = client.post(f"/api/v1/ingestion-runs/{run_id}/abort")
    second = client.post(f"/api/v1/ingestion-runs/{run_id}/abort")

    assert first.status_code == 200
    assert first.json()["status"] == "FAILED"
    assert second.status_code == 200
    assert len(fake_s3.aborts) == 1
    with testing_session() as session:
        run = session.get(IngestionRun, uuid.UUID(run_id))
        assert run.status == RunStatus.FAILED
        assert run.error_details["code"] == "UPLOAD_ABORTED"
