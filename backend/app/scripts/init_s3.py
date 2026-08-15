import logging
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.integrations.s3 import get_s3_client
from app.integrations.s3_setup import ensure_upload_bucket

logger = logging.getLogger(__name__)


def wait_for_s3(s3_client: Any, attempts: int = 30, delay_seconds: float = 1) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            s3_client.list_buckets()
            return
        except (BotoCoreError, ClientError) as exc:
            last_error = exc
            logger.info("waiting for Floci S3", extra={"attempt": attempt})
            if attempt < attempts:
                time.sleep(delay_seconds)
    raise RuntimeError("Floci S3 did not become ready") from last_error


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    s3_client = get_s3_client()
    wait_for_s3(s3_client)
    created = ensure_upload_bucket(
        s3_client,
        settings.s3_upload_bucket,
        settings.react_origin,
        settings.aws_default_region,
    )
    logger.info(
        "Floci upload bucket configured",
        extra={"bucket": settings.s3_upload_bucket, "bucket_created": created},
    )


if __name__ == "__main__":
    main()
