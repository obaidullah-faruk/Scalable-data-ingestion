from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config

from app.core.config import get_settings


def create_s3_client(endpoint_url: str) -> Any:
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        region_name=settings.aws_default_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


@lru_cache
def get_s3_client() -> Any:
    """Return the process-local client used for backend-to-Floci calls."""
    return create_s3_client(get_settings().floci_endpoint_url)


@lru_cache
def get_s3_presign_client() -> Any:
    """Return a signer whose generated URLs are reachable by the host browser."""
    return create_s3_client(get_settings().floci_browser_endpoint_url)
