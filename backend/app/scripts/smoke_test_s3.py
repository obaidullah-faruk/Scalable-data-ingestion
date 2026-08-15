import uuid
from xml.etree import ElementTree

import httpx
from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.integrations.s3 import get_s3_client
from app.integrations.s3_setup import bucket_cors_configuration
from app.services.multipart_uploads import CompletionPart, sign_completion_request


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


def smoke_test_browser_multipart_completion(
    s3_client: object,
    bucket_name: str,
    origin: str,
) -> None:
    key = f"smoke-tests/{uuid.uuid4()}.csv"
    payload = b"period,value\n2026-01-01,42\n"
    upload_id = None
    completed = False
    try:
        created = s3_client.create_multipart_upload(
            Bucket=bucket_name,
            Key=key,
            ContentType="text/csv",
        )
        upload_id = created["UploadId"]
        part_url = s3_client.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": bucket_name,
                "Key": key,
                "UploadId": upload_id,
                "PartNumber": 1,
            },
            ExpiresIn=60,
        )
        uploaded_part = httpx.put(
            part_url,
            content=payload,
            headers={"Origin": origin},
        )
        uploaded_part.raise_for_status()
        part_etag = uploaded_part.headers["etag"]
        manifest = [CompletionPart(part_number=1, etag=part_etag)]
        signed_completion = sign_completion_request(
            s3_client,
            bucket_name=bucket_name,
            object_key=key,
            upload_id=upload_id,
            parts=manifest,
        )
        preflight = httpx.options(
            signed_completion.url,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": ",".join(
                    header.lower() for header in signed_completion.headers
                ),
            },
        )
        preflight.raise_for_status()
        completion = httpx.post(
            signed_completion.url,
            content=signed_completion.body,
            headers={**signed_completion.headers, "Origin": origin},
        )
        completion.raise_for_status()
        root = ElementTree.fromstring(completion.content)
        completed_etag = next(
            (
                element.text
                for element in root.iter()
                if element.tag.rsplit("}", 1)[-1] == "ETag"
            ),
            None,
        )
        assert completed_etag, "The completion response did not contain an object ETag"
        metadata = s3_client.head_object(Bucket=bucket_name, Key=key)
        assert metadata["ContentLength"] == len(payload)
        assert metadata["ETag"] == completed_etag
        completed = True
    finally:
        if completed:
            s3_client.delete_object(Bucket=bucket_name, Key=key)
        elif upload_id is not None:
            try:
                s3_client.abort_multipart_upload(
                    Bucket=bucket_name,
                    Key=key,
                    UploadId=upload_id,
                )
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "NoSuchUpload":
                    raise


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
    smoke_test_browser_multipart_completion(
        s3_client,
        settings.s3_upload_bucket,
        settings.react_origin,
    )
    print("Floci S3 bucket, browser ETag, and multipart completion checks passed")


if __name__ == "__main__":
    main()
