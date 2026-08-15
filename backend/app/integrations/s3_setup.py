from typing import Any

from botocore.exceptions import ClientError


def bucket_cors_configuration(allowed_origin: str) -> dict[str, list[dict[str, Any]]]:
    return {
        "CORSRules": [
            {
                "ID": "browser-multipart-upload",
                "AllowedOrigins": [allowed_origin],
                "AllowedMethods": ["PUT", "POST", "HEAD"],
                "AllowedHeaders": ["*"],
                "ExposeHeaders": ["ETag"],
                "MaxAgeSeconds": 3600,
            }
        ]
    }


def ensure_upload_bucket(
    s3_client: Any,
    bucket_name: str,
    allowed_origin: str,
    region: str,
) -> bool:
    """Create the upload bucket if needed and replace its CORS policy.

    Returns True when this call created the bucket. Applying the same desired
    CORS policy on every run makes the operation safe and self-correcting.
    """
    created = False
    try:
        s3_client.head_bucket(Bucket=bucket_name)
    except ClientError as exc:
        error_code = str(exc.response.get("Error", {}).get("Code", ""))
        status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if error_code not in {"404", "NoSuchBucket", "NotFound"} and status_code != 404:
            raise

        create_arguments: dict[str, Any] = {"Bucket": bucket_name}
        if region != "us-east-1":
            create_arguments["CreateBucketConfiguration"] = {
                "LocationConstraint": region
            }
        s3_client.create_bucket(**create_arguments)
        created = True

    s3_client.put_bucket_cors(
        Bucket=bucket_name,
        CORSConfiguration=bucket_cors_configuration(allowed_origin),
    )
    return created
