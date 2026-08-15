import logging
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from xml.etree.ElementTree import Element, SubElement, tostring

from botocore.awsrequest import AWSRequest
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import IngestionRun, RunStatus, RunTask, RunTaskStatus, RunTaskType
from app.services.run_state import derive_run_state

logger = logging.getLogger(__name__)


class IngestionRunNotFoundError(Exception):
    pass


class InvalidUploadStateError(Exception):
    pass


class InvalidPartNumbersError(Exception):
    pass


class InvalidCompletionManifestError(Exception):
    pass


class ObjectVerificationError(Exception):
    pass


@dataclass(frozen=True)
class PresignedPart:
    part_number: int
    url: str


@dataclass(frozen=True)
class CompletionPart:
    part_number: int
    etag: str


@dataclass(frozen=True)
class SignedCompletion:
    method: str
    url: str
    headers: dict[str, str]
    body: str


def required_part_count(byte_size: int, part_size_bytes: int) -> int:
    return math.ceil(byte_size / part_size_bytes)


def build_completion_xml(parts: list[CompletionPart]) -> str:
    root = Element("CompleteMultipartUpload")
    for completed_part in parts:
        part = SubElement(root, "Part")
        SubElement(part, "PartNumber").text = str(completed_part.part_number)
        SubElement(part, "ETag").text = completed_part.etag
    return tostring(root, encoding="unicode", short_empty_elements=False)


def sign_completion_request(
    s3_client: Any,
    *,
    bucket_name: str,
    object_key: str,
    upload_id: str,
    parts: list[CompletionPart],
) -> SignedCompletion:
    body = build_completion_xml(parts)
    endpoint = s3_client.meta.endpoint_url.rstrip("/")
    url = (
        f"{endpoint}/{quote(bucket_name, safe='')}/{quote(object_key, safe='/')}"
        f"?uploadId={quote(upload_id, safe='')}"
    )
    request = AWSRequest(
        method="POST",
        url=url,
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/xml"},
    )
    # A header-based SigV4 signature includes the payload hash, so changing even
    # one part number or ETag invalidates this completion request.
    s3_client._request_signer.sign("CompleteMultipartUpload", request)
    return SignedCompletion(
        method="POST",
        url=url,
        headers=dict(request.headers.items()),
        body=body,
    )


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


def create_signed_completion_request(
    session: Session,
    presign_client: Any,
    *,
    run_id: uuid.UUID,
    bucket_name: str,
    upload_id: str,
    parts: list[CompletionPart],
    part_size_bytes: int,
) -> SignedCompletion:
    ingestion_run = session.get(IngestionRun, run_id)
    if ingestion_run is None:
        raise IngestionRunNotFoundError
    if ingestion_run.upload_confirmed_at is not None:
        raise InvalidUploadStateError("upload was already completed and confirmed")
    if ingestion_run.status not in {
        RunStatus.UPLOADING,
        RunStatus.AWAITING_CONFIRMATION,
    } or not ingestion_run.upload_id:
        raise InvalidUploadStateError(
            f"upload cannot be completed while run is {ingestion_run.status.value}"
        )
    if upload_id != ingestion_run.upload_id:
        raise InvalidCompletionManifestError("upload_id does not belong to this run")

    expected_part_count = required_part_count(
        ingestion_run.size_bytes, part_size_bytes
    )
    actual_part_numbers = [part.part_number for part in parts]
    expected_part_numbers = list(range(1, expected_part_count + 1))
    if actual_part_numbers != expected_part_numbers:
        raise InvalidCompletionManifestError(
            "parts must be ordered and contain every part number exactly once "
            f"(expected 1 through {expected_part_count})"
        )

    signed_completion = sign_completion_request(
        presign_client,
        bucket_name=bucket_name,
        object_key=ingestion_run.s3_key,
        upload_id=ingestion_run.upload_id,
        parts=parts,
    )

    ingestion_run.status = RunStatus.AWAITING_CONFIRMATION
    session.commit()
    return signed_completion


def confirm_completed_upload(
    session: Session,
    s3_client: Any,
    *,
    run_id: uuid.UUID,
    bucket_name: str,
    object_etag: str,
    object_version_id: str | None,
) -> IngestionRun:
    ingestion_run = session.get(IngestionRun, run_id)
    if ingestion_run is None:
        raise IngestionRunNotFoundError
    if ingestion_run.upload_confirmed_at is not None:
        if (
            ingestion_run.object_etag == object_etag
            and (
                object_version_id is None
                or ingestion_run.object_version_id == object_version_id
            )
        ):
            return ingestion_run
        raise InvalidUploadStateError("run was already confirmed with another object")
    if ingestion_run.status != RunStatus.AWAITING_CONFIRMATION:
        raise InvalidUploadStateError(
            f"upload cannot be confirmed while run is {ingestion_run.status.value}"
        )

    try:
        metadata = s3_client.head_object(
            Bucket=bucket_name,
            Key=ingestion_run.s3_key,
        )
    except ClientError as exc:
        error_code = str(exc.response.get("Error", {}).get("Code", ""))
        status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if error_code in {"404", "NoSuchKey", "NotFound"} or status_code == 404:
            raise ObjectVerificationError(
                "completed object does not exist yet; S3 may still have an incomplete upload"
            ) from exc
        raise

    content_length = metadata.get("ContentLength")
    if content_length != ingestion_run.size_bytes:
        raise ObjectVerificationError(
            f"completed object has {content_length} bytes; expected {ingestion_run.size_bytes}"
        )

    stored_etag = metadata.get("ETag")
    stored_version_id = metadata.get("VersionId")
    if stored_etag != object_etag:
        raise ObjectVerificationError("completed object's ETag does not match S3")
    if object_version_id is not None and stored_version_id != object_version_id:
        raise ObjectVerificationError("completed object's version does not match S3")

    ingestion_run.uploaded_bytes = ingestion_run.size_bytes
    ingestion_run.object_etag = stored_etag
    ingestion_run.object_version_id = stored_version_id
    ingestion_run.upload_confirmed_at = datetime.now(UTC)
    ingestion_run.error_details = None
    session.commit()
    session.refresh(ingestion_run)
    return ingestion_run


def confirm_and_queue_completed_upload(
    session: Session,
    s3_client: Any,
    *,
    run_id: uuid.UUID,
    bucket_name: str,
    object_etag: str,
    object_version_id: str | None,
) -> tuple[IngestionRun, bool]:
    """Verify an uploaded object and atomically create its durable work rows.

    The boolean indicates whether this call performed the confirmation. A
    repeated confirmation deliberately returns ``False`` so the API does not
    publish another set of Celery messages.
    """
    with session.begin():
        ingestion_run = session.get(IngestionRun, run_id, with_for_update=True)
        if ingestion_run is None:
            raise IngestionRunNotFoundError
        if ingestion_run.upload_confirmed_at is not None:
            if (
                ingestion_run.object_etag == object_etag
                and (
                    object_version_id is None
                    or ingestion_run.object_version_id == object_version_id
                )
            ):
                return ingestion_run, False
            raise InvalidUploadStateError("run was already confirmed with another object")
        if ingestion_run.status != RunStatus.AWAITING_CONFIRMATION:
            raise InvalidUploadStateError(
                f"upload cannot be confirmed while run is {ingestion_run.status.value}"
            )

        try:
            metadata = s3_client.head_object(
                Bucket=bucket_name,
                Key=ingestion_run.s3_key,
            )
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if error_code in {"404", "NoSuchKey", "NotFound"} or status_code == 404:
                raise ObjectVerificationError(
                    "completed object does not exist yet; S3 may still have an incomplete upload"
                ) from exc
            raise

        content_length = metadata.get("ContentLength")
        if content_length != ingestion_run.size_bytes:
            raise ObjectVerificationError(
                f"completed object has {content_length} bytes; expected {ingestion_run.size_bytes}"
            )

        stored_etag = metadata.get("ETag")
        stored_version_id = metadata.get("VersionId")
        if stored_etag != object_etag:
            raise ObjectVerificationError("completed object's ETag does not match S3")
        if object_version_id is not None and stored_version_id != object_version_id:
            raise ObjectVerificationError("completed object's version does not match S3")

        ingestion_run.uploaded_bytes = ingestion_run.size_bytes
        ingestion_run.object_etag = stored_etag
        ingestion_run.object_version_id = stored_version_id
        ingestion_run.upload_confirmed_at = datetime.now(UTC)
        ingestion_run.error_details = None

        existing_tasks = {
            task.task_type: task
            for task in session.scalars(
                select(RunTask).where(RunTask.ingestion_run_id == ingestion_run.id)
            )
        }
        task_rows = list(existing_tasks.values())
        for task_type in RunTaskType:
            task = existing_tasks.get(task_type)
            if task is None:
                task = RunTask(
                    ingestion_run_id=ingestion_run.id,
                    task_type=task_type,
                    status=RunTaskStatus.QUEUED,
                )
                session.add(task)
                task_rows.append(task)

        session.flush()
        derive_run_state(ingestion_run, task_rows)

    return ingestion_run, True


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
    if ingestion_run.upload_confirmed_at is not None:
        raise InvalidUploadStateError("a confirmed upload cannot be aborted")
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
