from io import BytesIO

import pytest
from botocore.exceptions import ClientError

from app.integrations.s3_setup import (
    bucket_cors_configuration,
    ensure_upload_bucket,
)
from app.scripts.smoke_test_s3 import smoke_test_object_round_trip


class FakeS3Client:
    def __init__(self, bucket_exists: bool = False) -> None:
        self.bucket_exists = bucket_exists
        self.create_calls: list[dict] = []
        self.cors_calls: list[dict] = []
        self.objects: dict[tuple[str, str], bytes] = {}

    def head_bucket(self, **arguments: str) -> None:
        if not self.bucket_exists:
            raise ClientError(
                {
                    "Error": {"Code": "404", "Message": "Not Found"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "HeadBucket",
            )

    def create_bucket(self, **arguments: object) -> None:
        self.create_calls.append(arguments)
        self.bucket_exists = True

    def put_bucket_cors(self, **arguments: object) -> None:
        self.cors_calls.append(arguments)

    def put_object(self, Bucket: str, Key: str, Body: bytes) -> None:
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket: str, Key: str) -> dict:
        return {"Body": BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, Bucket: str, Key: str) -> None:
        self.objects.pop((Bucket, Key), None)


def test_bucket_setup_is_idempotent() -> None:
    client = FakeS3Client()

    first_created = ensure_upload_bucket(
        client, "uploads", "http://localhost:3000", "us-east-1"
    )
    second_created = ensure_upload_bucket(
        client, "uploads", "http://localhost:3000", "us-east-1"
    )

    assert first_created is True
    assert second_created is False
    assert client.create_calls == [{"Bucket": "uploads"}]
    assert len(client.cors_calls) == 2


def test_bucket_cors_allows_multipart_browser_contract() -> None:
    rule = bucket_cors_configuration("http://localhost:3000")["CORSRules"][0]

    assert rule["AllowedOrigins"] == ["http://localhost:3000"]
    assert rule["AllowedMethods"] == ["PUT", "POST", "HEAD"]
    assert rule["AllowedHeaders"] == ["*"]
    assert rule["ExposeHeaders"] == ["ETag"]


def test_unexpected_head_bucket_error_is_not_hidden() -> None:
    class ForbiddenClient(FakeS3Client):
        def head_bucket(self, **arguments: str) -> None:
            raise ClientError(
                {
                    "Error": {"Code": "AccessDenied", "Message": "Forbidden"},
                    "ResponseMetadata": {"HTTPStatusCode": 403},
                },
                "HeadBucket",
            )

    with pytest.raises(ClientError):
        ensure_upload_bucket(
            ForbiddenClient(), "uploads", "http://localhost:3000", "us-east-1"
        )


def test_object_round_trip_smoke_test_cleans_up() -> None:
    client = FakeS3Client(bucket_exists=True)

    smoke_test_object_round_trip(client, "uploads")

    assert client.objects == {}
