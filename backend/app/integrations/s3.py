from typing import Any

import boto3
from botocore.config import Config

from app.core.config import Settings, get_settings


def get_s3_client(settings: Settings | None = None) -> Any:
    settings = settings or get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.floci_endpoint_url,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        region_name=settings.aws_default_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
