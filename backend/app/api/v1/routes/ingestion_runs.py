import json
import uuid
from collections.abc import Iterator
from typing import Annotated, Any

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db_session
from app.integrations.s3 import get_s3_client, get_s3_presign_client
from app.schemas.ingestion_runs import (
    AbortIngestionRunResponse,
    CompletionRequest,
    ConfirmUploadRequest,
    ConfirmUploadResponse,
    CreateIngestionRunRequest,
    CreateIngestionRunResponse,
    PartUrlsRequest,
    PartUrlsResponse,
    PresignedPartUrl,
    SignedCompletionRequest,
    IngestionRunSnapshotResponse,
)
from app.services.multipart_uploads import (
    CompletionPart,
    IngestionRunNotFoundError,
    InvalidCompletionManifestError,
    InvalidPartNumbersError,
    InvalidUploadStateError,
    ObjectVerificationError,
    abort_multipart_upload,
    confirm_and_queue_completed_upload,
    create_presigned_part_urls,
    create_signed_completion_request,
    required_part_count,
    start_multipart_upload,
)
from app.services.task_dispatch import dispatch_queued_tasks
from app.services.progress_events import PROGRESS_CHANNEL, get_progress_subscriber
from app.services.run_snapshots import get_run_snapshot, is_terminal_run_status

router = APIRouter(prefix="/api/v1/ingestion-runs", tags=["ingestion-runs"])

DatabaseSession = Annotated[Session, Depends(get_db_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]
S3Client = Annotated[Any, Depends(get_s3_client)]
S3PresignClient = Annotated[Any, Depends(get_s3_presign_client)]


def s3_unavailable_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Local object storage request failed",
    )


def sse_message(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def run_event_stream(
    run_id: uuid.UUID, initial_snapshot: IngestionRunSnapshotResponse
) -> Iterator[str]:
    """Yield a durable snapshot first, then matching Redis progress events."""
    snapshot_data = initial_snapshot.model_dump(mode="json")
    yield sse_message("snapshot", snapshot_data)
    if is_terminal_run_status(initial_snapshot.status.value):
        return

    redis_client = get_progress_subscriber()
    pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
    try:
        pubsub.subscribe(PROGRESS_CHANNEL)
        while True:
            message = pubsub.get_message(timeout=15)
            if message is None:
                yield ": keepalive\n\n"
                continue
            if message.get("type") != "message":
                continue
            try:
                progress = json.loads(message["data"])
            except (TypeError, json.JSONDecodeError):
                continue
            if progress.get("run_id") != str(run_id):
                continue
            yield sse_message("progress", progress)
            if is_terminal_run_status(str(progress.get("run_status", ""))):
                return
    except RedisError:
        # Closing the stream makes EventSource reconnect; the client fetches a
        # fresh PostgreSQL snapshot in its next onopen callback.
        return
    finally:
        try:
            pubsub.close()
            redis_client.close()
        except RedisError:
            pass


@router.get("/{run_id}", response_model=IngestionRunSnapshotResponse)
def get_ingestion_run(
    run_id: uuid.UUID,
    session: DatabaseSession,
) -> IngestionRunSnapshotResponse:
    snapshot = get_run_snapshot(session, run_id)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ingestion run not found",
        )
    return snapshot


@router.get("/{run_id}/events")
def ingestion_run_events(
    run_id: uuid.UUID,
    session: DatabaseSession,
) -> StreamingResponse:
    snapshot = get_run_snapshot(session, run_id)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ingestion run not found",
        )
    return StreamingResponse(
        run_event_stream(run_id, snapshot),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("", response_model=CreateIngestionRunResponse, status_code=status.HTTP_201_CREATED)
def create_ingestion_run(
    request: CreateIngestionRunRequest,
    session: DatabaseSession,
    s3_client: S3Client,
    settings: AppSettings,
) -> CreateIngestionRunResponse:
    if request.byte_size > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"file exceeds the {settings.max_upload_size_bytes}-byte limit",
        )
    total_parts = required_part_count(
        request.byte_size, settings.upload_part_size_bytes
    )
    if total_parts > 10_000:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="file would require more than 10000 multipart parts",
        )

    try:
        ingestion_run = start_multipart_upload(
            session,
            s3_client,
            bucket_name=settings.s3_upload_bucket,
            filename=request.filename,
            content_type=request.content_type,
            byte_size=request.byte_size,
        )
    except (BotoCoreError, ClientError) as exc:
        raise s3_unavailable_error() from exc

    return CreateIngestionRunResponse(
        run_id=ingestion_run.id,
        status=ingestion_run.status,
        object_key=ingestion_run.s3_key,
        upload_id=ingestion_run.upload_id,
        part_size_bytes=settings.upload_part_size_bytes,
        total_parts=total_parts,
        part_url_batch_limit=settings.part_url_batch_limit,
        part_urls_endpoint=f"/api/v1/ingestion-runs/{ingestion_run.id}/part-urls",
    )


@router.post("/{run_id}/part-urls", response_model=PartUrlsResponse)
def create_part_urls(
    run_id: uuid.UUID,
    request: PartUrlsRequest,
    session: DatabaseSession,
    presign_client: S3PresignClient,
    settings: AppSettings,
) -> PartUrlsResponse:
    try:
        parts = create_presigned_part_urls(
            session,
            presign_client,
            run_id=run_id,
            bucket_name=settings.s3_upload_bucket,
            part_numbers=request.part_numbers,
            part_size_bytes=settings.upload_part_size_bytes,
            batch_limit=settings.part_url_batch_limit,
            expires_in_seconds=settings.presigned_url_expiration_seconds,
        )
    except IngestionRunNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ingestion run not found",
        ) from exc
    except InvalidPartNumbersError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except InvalidUploadStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (BotoCoreError, ClientError) as exc:
        raise s3_unavailable_error() from exc

    return PartUrlsResponse(
        run_id=run_id,
        expires_in_seconds=settings.presigned_url_expiration_seconds,
        parts=[
            PresignedPartUrl(part_number=part.part_number, url=part.url)
            for part in parts
        ],
    )


@router.post(
    "/{run_id}/completion-request",
    response_model=SignedCompletionRequest,
)
def create_completion_request(
    run_id: uuid.UUID,
    request: CompletionRequest,
    session: DatabaseSession,
    presign_client: S3PresignClient,
    settings: AppSettings,
) -> SignedCompletionRequest:
    try:
        completion = create_signed_completion_request(
            session,
            presign_client,
            run_id=run_id,
            bucket_name=settings.s3_upload_bucket,
            upload_id=request.upload_id,
            parts=[
                CompletionPart(part_number=part.part_number, etag=part.etag)
                for part in request.parts
            ],
            part_size_bytes=settings.upload_part_size_bytes,
        )
    except IngestionRunNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ingestion run not found",
        ) from exc
    except InvalidCompletionManifestError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except InvalidUploadStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (BotoCoreError, ClientError) as exc:
        raise s3_unavailable_error() from exc

    return SignedCompletionRequest(
        method=completion.method,
        url=completion.url,
        headers=completion.headers,
        body=completion.body,
    )


@router.post("/{run_id}/confirm-upload", response_model=ConfirmUploadResponse)
def confirm_upload(
    run_id: uuid.UUID,
    request: ConfirmUploadRequest,
    session: DatabaseSession,
    s3_client: S3Client,
    settings: AppSettings,
) -> ConfirmUploadResponse:
    try:
        ingestion_run, newly_confirmed = confirm_and_queue_completed_upload(
            session,
            s3_client,
            run_id=run_id,
            bucket_name=settings.s3_upload_bucket,
            object_etag=request.object_etag,
            object_version_id=request.object_version_id,
        )
    except IngestionRunNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ingestion run not found",
        ) from exc
    except (InvalidUploadStateError, ObjectVerificationError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (BotoCoreError, ClientError) as exc:
        raise s3_unavailable_error() from exc

    if newly_confirmed:
        dispatch_queued_tasks(session, run_id=run_id)
        session.refresh(ingestion_run)

    return ConfirmUploadResponse(
        run_id=ingestion_run.id,
        status=ingestion_run.status,
        object_etag=ingestion_run.object_etag,
        object_version_id=ingestion_run.object_version_id,
        tasks=[
            {
                "task_type": task.task_type,
                "status": task.status,
                "celery_task_id": task.celery_task_id,
            }
            for task in sorted(ingestion_run.tasks, key=lambda task: task.task_type.value)
        ],
    )


@router.post("/{run_id}/abort", response_model=AbortIngestionRunResponse)
def abort_ingestion_run(
    run_id: uuid.UUID,
    session: DatabaseSession,
    s3_client: S3Client,
    settings: AppSettings,
) -> AbortIngestionRunResponse:
    try:
        ingestion_run = abort_multipart_upload(
            session,
            s3_client,
            run_id=run_id,
            bucket_name=settings.s3_upload_bucket,
        )
    except IngestionRunNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ingestion run not found",
        ) from exc
    except InvalidUploadStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (BotoCoreError, ClientError) as exc:
        raise s3_unavailable_error() from exc

    return AbortIngestionRunResponse(
        run_id=ingestion_run.id,
        status=ingestion_run.status,
    )
