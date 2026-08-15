import uuid

import httpx

from app.core.config import get_settings
from app.integrations.s3 import get_s3_client
from app.integrations.s3_setup import bucket_cors_configuration


def assert_bucket_cors(s3_client: object, bucket_name: str, origin: str) -> None:
    response = s3_client.get_bucket_cors(Bucket=bucket_name)
    expected_rule = bucket_cors_configuration(origin)["CORSRules"][0]
    actual_rule = response["CORSRules"][0]
    for field, expected in expected_rule.items():
        assert actual_rule[field] == expected, f"Unexpected CORS {field}: {actual_rule[field]}"


def smoke_test_object_round_trip(s3_client: object, bucket_name: str) -> None:
    key = f"smoke-tests/{uuid.uuid4()}.txt"
    payload = b"Floci S3 smoke test"
    try:
        s3_client.put_object(Bucket=bucket_name, Key=key, Body=payload)
        response = s3_client.get_object(Bucket=bucket_name, Key=key)
        assert response["Body"].read() == payload
    finally:
        s3_client.delete_object(Bucket=bucket_name, Key=key)


def smoke_test_browser_etag(
    s3_client: object,
    bucket_name: str,
    origin: str,
) -> None:
    key = f"smoke-tests/{uuid.uuid4()}.txt"
    url = s3_client.generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket_name, "Key": key},
        ExpiresIn=60,
    )
    try:
        preflight = httpx.options(
            url,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        preflight.raise_for_status()

        upload = httpx.put(
            url,
            content=b"browser-style upload",
            headers={"Origin": origin, "Content-Type": "text/plain"},
        )
        upload.raise_for_status()
        assert upload.headers.get("etag"), "The upload response did not contain ETag"
        exposed_headers = upload.headers.get("access-control-expose-headers", "")
        assert "etag" in exposed_headers.lower(), "The browser cannot read the ETag header"
    finally:
        s3_client.delete_object(Bucket=bucket_name, Key=key)


def main() -> None:
    settings = get_settings()
    s3_client = get_s3_client()
    assert_bucket_cors(s3_client, settings.s3_upload_bucket, settings.react_origin)
    smoke_test_object_round_trip(s3_client, settings.s3_upload_bucket)
    smoke_test_browser_etag(
        s3_client,
        settings.s3_upload_bucket,
        settings.react_origin,
    )
    print("Floci S3 bucket, object round-trip, and browser ETag checks passed")


if __name__ == "__main__":
    main()
