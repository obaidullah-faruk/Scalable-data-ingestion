import logging
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.orm import Session

from app.models import IngestionRun, RunStatus

logger = logging.getLogger(__name__)


class IngestionRunNotFoundError(Exception):
    pass


class InvalidUploadStateError(Exception):
    pass


class InvalidPartNumbersError(Exception):
    pass


@dataclass(frozen=True)
class PresignedPart:
    part_number: int
    url: str


def required_part_count(byte_size: int, part_size_bytes: int) -> int:
    return math.ceil(byte_size / part_size_bytes)


def start_multipart_upload(
    session: Session,
    s3_client: Any,
    *,
    bucket_name: str,
    filename: str,
    content_type: str,
    byte_size: int,
) -> IngestionRun:
    run_id = uuid.uuid4()
    object_key = f"uploads/{run_id}/source.csv"
    upload_id: str | None = None

    try:
        response = s3_client.create_multipart_upload(
            Bucket=bucket_name,
            Key=object_key,
            ContentType=content_type,
            Metadata={"ingestion-run-id": str(run_id)},
        )
        upload_id = response["UploadId"]
        ingestion_run = IngestionRun(
            id=run_id,
            status=RunStatus.UPLOADING,
            original_filename=filename,
            s3_key=object_key,
            upload_id=upload_id,
            size_bytes=byte_size,
        )
        session.add(ingestion_run)
        session.commit()
        session.refresh(ingestion_run)
        return ingestion_run
    except Exception:
        session.rollback()
        if upload_id is not None:
            try:
                s3_client.abort_multipart_upload(
                    Bucket=bucket_name,
                    Key=object_key,
                    UploadId=upload_id,
                )
            except (BotoCoreError, ClientError):
                logger.exception("Could not clean up multipart upload after database failure")
        raise


def create_presigned_part_urls(
    session: Session,
    presign_client: Any,
    *,
    run_id: uuid.UUID,
    bucket_name: str,
    part_numbers: list[int],
    part_size_bytes: int,
    batch_limit: int,
    expires_in_seconds: int,
) -> list[PresignedPart]:
    ingestion_run = session.get(IngestionRun, run_id)
    if ingestion_run is None:
        raise IngestionRunNotFoundError
    if ingestion_run.status != RunStatus.UPLOADING or not ingestion_run.upload_id:
        raise InvalidUploadStateError(
            f"part URLs cannot be created while run is {ingestion_run.status.value}"
        )
    if len(part_numbers) > batch_limit:
        raise InvalidPartNumbersError(
            f"at most {batch_limit} part URLs may be requested at once"
        )

    total_parts = required_part_count(ingestion_run.size_bytes, part_size_bytes)
    invalid_numbers = sorted(number for number in part_numbers if number > total_parts)
    if invalid_numbers:
        raise InvalidPartNumbersError(
            f"part numbers exceed this upload's {total_parts} required parts: {invalid_numbers}"
        )

    return [
        PresignedPart(
            part_number=part_number,
            url=presign_client.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": bucket_name,
                    "Key": ingestion_run.s3_key,
                    "UploadId": ingestion_run.upload_id,
                    "PartNumber": part_number,
                },
                ExpiresIn=expires_in_seconds,
            ),
        )
        for part_number in sorted(part_numbers)
    ]


def abort_multipart_upload(
    session: Session,
    s3_client: Any,
    *,
    run_id: uuid.UUID,
    bucket_name: str,
) -> IngestionRun:
    ingestion_run = session.get(IngestionRun, run_id)
    if ingestion_run is None:
        raise IngestionRunNotFoundError
    if (
        ingestion_run.status == RunStatus.FAILED
        and ingestion_run.error_details
        and ingestion_run.error_details.get("code") == "UPLOAD_ABORTED"
    ):
        return ingestion_run
    if ingestion_run.status not in {
        RunStatus.UPLOADING,
        RunStatus.AWAITING_CONFIRMATION,
    } or not ingestion_run.upload_id:
        raise InvalidUploadStateError(
            f"run cannot be aborted while it is {ingestion_run.status.value}"
        )

    try:
        s3_client.abort_multipart_upload(
            Bucket=bucket_name,
            Key=ingestion_run.s3_key,
            UploadId=ingestion_run.upload_id,
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "NoSuchUpload":
            raise

    ingestion_run.status = RunStatus.FAILED
    ingestion_run.error_details = {
        "code": "UPLOAD_ABORTED",
        "message": "Multipart upload was aborted before confirmation",
    }
    ingestion_run.completed_at = datetime.now(UTC)
    session.commit()
    session.refresh(ingestion_run)
    return ingestion_run
