import uuid
from collections.abc import Generator
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
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
from app.models import (
    Base,
    GdpSeriesSummary,
    IngestionRun,
    RunStatus,
    RunTask,
    RunTaskStatus,
    RunTaskType,
    RunValidationProfile,
)
from app.services import task_dispatch


class FakeS3Client:
    def __init__(self) -> None:
        self.uploads: list[dict] = []
        self.aborts: list[dict] = []
        self.completed_objects: dict[str, dict[str, object]] = {}
        self.meta = SimpleNamespace(endpoint_url="http://localhost:4566")
        self._request_signer = FakeRequestSigner()

    def create_multipart_upload(self, **arguments: object) -> dict[str, str]:
        self.uploads.append(arguments)
        return {"UploadId": f"upload-{len(self.uploads)}"}

    def generate_presigned_url(
        self,
        operation: str,
        *,
        Params: dict[str, object],
        ExpiresIn: int,
        HttpMethod: str | None = None,
    ) -> str:
        query_values = {
            "uploadId": Params["UploadId"],
            "expires": ExpiresIn,
        }
        if operation == "upload_part":
            query_values["partNumber"] = Params["PartNumber"]
        query = urlencode(query_values)
        return (
            f"http://localhost:4566/{Params['Bucket']}/{Params['Key']}?{query}"
        )

    def abort_multipart_upload(self, **arguments: object) -> None:
        self.aborts.append(arguments)

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        return self.completed_objects[Key]


class FakeRequestSigner:
    def sign(self, operation_name: str, request: object) -> None:
        assert operation_name == "CompleteMultipartUpload"
        request.headers["Authorization"] = "test-signature"
        request.headers["X-Amz-Date"] = "20260816T000000Z"


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


def test_completion_is_signed_then_verified_with_head_object(
    api_context, monkeypatch
) -> None:
    client, fake_s3, testing_session = api_context
    dispatched: list[tuple[str, dict[str, str]]] = []

    def send_task(task_name: str, *, kwargs: dict[str, str]):
        dispatched.append((task_name, kwargs))
        return SimpleNamespace(id=f"celery-{len(dispatched)}")

    monkeypatch.setattr(task_dispatch.celery_app, "send_task", send_task)
    run = create_run(client).json()
    parts = [
        {"part_number": number, "etag": f'"part-{number}"'}
        for number in range(1, 4)
    ]

    completion = client.post(
        f"/api/v1/ingestion-runs/{run['run_id']}/completion-request",
        json={"upload_id": run["upload_id"], "parts": parts},
    )

    assert completion.status_code == 200
    signed_request = completion.json()
    assert signed_request["method"] == "POST"
    assert signed_request["headers"]["Content-Type"] == "application/xml"
    assert signed_request["headers"]["Authorization"] == "test-signature"
    assert signed_request["body"].index("part-1") < signed_request["body"].index("part-3")
    fake_s3.completed_objects[run["object_key"]] = {
        "ContentLength": 20_000_000,
        "ETag": '"completed-etag-3"',
        "VersionId": "version-1",
    }

    confirmed = client.post(
        f"/api/v1/ingestion-runs/{run['run_id']}/confirm-upload",
        json={
            "object_etag": '"completed-etag-3"',
            "object_version_id": "version-1",
        },
    )
    repeated = client.post(
        f"/api/v1/ingestion-runs/{run['run_id']}/confirm-upload",
        json={
            "object_etag": '"completed-etag-3"',
            "object_version_id": "version-1",
        },
    )
    abort_confirmed = client.post(
        f"/api/v1/ingestion-runs/{run['run_id']}/abort"
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "QUEUED"
    assert len(confirmed.json()["tasks"]) == 3
    assert {task["task_type"] for task in confirmed.json()["tasks"]} == {
        task_type.value for task_type in RunTaskType
    }
    assert repeated.status_code == 200
    assert repeated.json()["tasks"] == confirmed.json()["tasks"]
    assert len(dispatched) == 3
    assert {task_name for task_name, _ in dispatched} == set(
        task_dispatch.CELERY_TASK_NAMES.values()
    )
    assert abort_confirmed.status_code == 409
    with testing_session() as session:
        persisted = session.get(IngestionRun, uuid.UUID(run["run_id"]))
        assert persisted.uploaded_bytes == persisted.size_bytes
        assert persisted.object_etag == '"completed-etag-3"'
        assert persisted.upload_confirmed_at is not None
        task_rows = session.query(RunTask).filter_by(ingestion_run_id=persisted.id).all()
        assert len(task_rows) == 3
        assert all(task.celery_task_id for task in task_rows)


def test_completion_rejects_missing_or_unordered_parts(api_context) -> None:
    client, _, _ = api_context
    run = create_run(client).json()

    response = client.post(
        f"/api/v1/ingestion-runs/{run['run_id']}/completion-request",
        json={
            "upload_id": run["upload_id"],
            "parts": [
                {"part_number": 2, "etag": '"part-2"'},
                {"part_number": 1, "etag": '"part-1"'},
            ],
        },
    )

    assert response.status_code == 422
    assert "ordered" in response.json()["detail"]


def test_confirmation_rejects_wrong_object_size(api_context) -> None:
    client, fake_s3, _ = api_context
    run = create_run(client, byte_size=1).json()
    client.post(
        f"/api/v1/ingestion-runs/{run['run_id']}/completion-request",
        json={
            "upload_id": run["upload_id"],
            "parts": [{"part_number": 1, "etag": '"part-1"'}],
        },
    )
    fake_s3.completed_objects[run["object_key"]] = {
        "ContentLength": 2,
        "ETag": '"completed"',
    }

    response = client.post(
        f"/api/v1/ingestion-runs/{run['run_id']}/confirm-upload",
        json={"object_etag": '"completed"'},
    )

    assert response.status_code == 409
    assert "expected 1" in response.json()["detail"]


def test_run_snapshot_and_terminal_sse_are_durable(api_context) -> None:
    client, _, testing_session = api_context
    run_id = uuid.uuid4()
    with testing_session() as session:
        run = IngestionRun(
            id=run_id,
            status=RunStatus.SUCCEEDED,
            original_filename="gdp.csv",
            s3_key=f"uploads/{run_id}/source.csv",
            size_bytes=100,
            uploaded_bytes=100,
            processing_progress_percent=100,
            completed_task_count=3,
            total_task_count=3,
        )
        session.add(run)
        session.add_all(
            [
                RunTask(
                    ingestion_run_id=run_id,
                    task_type=task_type,
                    status=RunTaskStatus.SUCCEEDED,
                    progress_percent=100,
                    processed_rows=10,
                    celery_task_id=f"celery-{index}",
                )
                for index, task_type in enumerate(RunTaskType, start=1)
            ]
        )
        session.add(
            RunValidationProfile(
                ingestion_run_id=run_id,
                row_count=10,
                missing_data_value_count=1,
                findings={"header": {"valid": True}},
            )
        )
        session.add(
            GdpSeriesSummary(
                ingestion_run_id=run_id,
                series_reference="GDP",
                units="Dollars",
                valid_observation_count=10,
                first_period=date(2020, 3, 1),
                first_value=Decimal("100"),
                latest_period=date(2020, 6, 1),
                latest_value=Decimal("105"),
                min_value=Decimal("100"),
                max_value=Decimal("105"),
                quarter_to_quarter_change=Decimal("5"),
            )
        )
        session.commit()

    snapshot = client.get(f"/api/v1/ingestion-runs/{run_id}")
    events = client.get(f"/api/v1/ingestion-runs/{run_id}/events")

    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["status"] == "SUCCEEDED"
    assert len(body["tasks"]) == 3
    assert body["validation_profile"]["row_count"] == 10
    assert body["series_summaries"] == [
        {
            "series_reference": "GDP",
            "units": "Dollars",
            "valid_observation_count": 10,
            "first_period": "2020-03-01",
            "first_value": "100.00000000",
            "latest_period": "2020-06-01",
            "latest_value": "105.00000000",
            "min_value": "100.00000000",
            "max_value": "105.00000000",
            "quarter_to_quarter_change": "5.00000000",
        }
    ]
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert "event: snapshot" in events.text
    assert '"status": "SUCCEEDED"' in events.text
