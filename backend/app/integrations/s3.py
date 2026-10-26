from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config

from app.core.config import get_settings


def create_s3_client(endpoint_url: str | None) -> Any:
    """Create an S3 client. Credentials come from boto3's default chain."""
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=get_settings().aws_default_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


@lru_cache
def get_s3_client() -> Any:
    """Return the process-local client used for backend-to-S3 calls."""
    return create_s3_client(get_settings().s3_endpoint_url)


@lru_cache
def get_s3_presign_client() -> Any:
    """Return a signer whose generated URLs are reachable by the browser."""
    settings = get_settings()
    return create_s3_client(settings.s3_browser_endpoint_url or settings.s3_endpoint_url)
