"""Celery implementations for the three independent CSV ingestion tasks."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Callable
import uuid

from botocore.exceptions import BotoCoreError, ClientError
from celery import Task
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.integrations.s3 import get_s3_client
from app.models import IngestionRun, RunTask, RunTaskStatus, RunTaskType
from app.services.csv_processing import (
    ParsedRow,
    SeriesAccumulator,
    ValidationCounts,
    parse_valid_row,
    progress_from_bytes,
    require_expected_header,
    stream_csv_rows,
    upsert_observations,
    upsert_series_summaries,
    upsert_validation_profile,
    validate_row,
)
from app.services.progress_events import publish_progress_event
from app.services.run_state import refresh_run_state
from app.workers.celery_app import celery_app

RETRIABLE_EXCEPTIONS = (BotoCoreError, ClientError, OSError, SQLAlchemyError)


def _task_snapshot(run: IngestionRun, task: RunTask) -> dict[str, Any]:
    return {
        "run_id": str(run.id),
        "run_status": run.status.value,
        "processing_progress_percent": run.processing_progress_percent,
        "task_id": str(task.id),
        "task_type": task.task_type.value,
        "task_status": task.status.value,
        "task_progress_percent": task.progress_percent,
        "processed_rows": task.processed_rows,
    }


def _commit_and_publish(session: Session, task: RunTask) -> None:
    run = refresh_run_state(session, task.ingestion_run_id)
    session.commit()
    publish_progress_event(_task_snapshot(run, task))


def _get_task(
    session: Session,
    *,
    ingestion_run_id: uuid.UUID,
    run_task_id: uuid.UUID,
    task_type: RunTaskType,
) -> tuple[IngestionRun, RunTask]:
    task = session.get(RunTask, run_task_id)
    if (
        task is None
        or task.ingestion_run_id != ingestion_run_id
        or task.task_type != task_type
    ):
        raise ValueError("Celery task does not match the durable ingestion task")
    run = session.get(IngestionRun, ingestion_run_id)
    if run is None:
        raise ValueError("Ingestion run does not exist")
    return run, task


def _start_task(
    session: Session,
    *,
    ingestion_run_id: uuid.UUID,
    run_task_id: uuid.UUID,
    task_type: RunTaskType,
) -> tuple[IngestionRun, RunTask] | None:
    run, task = _get_task(
        session,
        ingestion_run_id=ingestion_run_id,
        run_task_id=run_task_id,
        task_type=task_type,
    )
    if task.status in {RunTaskStatus.SUCCEEDED, RunTaskStatus.FAILED}:
        return None
    task.status = RunTaskStatus.PROCESSING
    task.started_at = task.started_at or datetime.now(UTC)
    task.error_details = None
    _commit_and_publish(session, task)
    return run, task


def checkpoint_task(
    session: Session,
    *,
    run_task_id: uuid.UUID,
    processed_rows: int,
    progress_percent: int,
) -> None:
    task = session.get(RunTask, run_task_id)
    if task is None:
        raise ValueError("Ingestion task does not exist")
    task.processed_rows = max(task.processed_rows, processed_rows)
    task.progress_percent = max(task.progress_percent, min(99, progress_percent))
    task.status = RunTaskStatus.PROCESSING
    _commit_and_publish(session, task)


def _finish_task(session: Session, *, run_task_id: uuid.UUID, processed_rows: int) -> None:
    task = session.get(RunTask, run_task_id)
    if task is None:
        raise ValueError("Ingestion task does not exist")
    task.processed_rows = max(task.processed_rows, processed_rows)
    task.progress_percent = 100
    task.status = RunTaskStatus.SUCCEEDED
    task.completed_at = datetime.now(UTC)
    task.error_details = None
    _commit_and_publish(session, task)


def _mark_retry(
    session: Session,
    *,
    run_task_id: uuid.UUID,
    exception: Exception,
) -> None:
    task = session.get(RunTask, run_task_id)
    if task is None:
        return
    task.status = RunTaskStatus.QUEUED
    task.retry_count += 1
    task.error_details = {"code": "RETRYING", "message": str(exception)}
    _commit_and_publish(session, task)


def _fail_task(session: Session, *, run_task_id: uuid.UUID, exception: Exception) -> None:
    task = session.get(RunTask, run_task_id)
    if task is None:
        return
    task.status = RunTaskStatus.FAILED
    task.completed_at = datetime.now(UTC)
    task.error_details = {
        "code": type(exception).__name__,
        "message": str(exception),
    }
    _commit_and_publish(session, task)


def _checkpoint_due(processed_rows: int, checkpoint_rows: int) -> bool:
    return processed_rows > 0 and processed_rows % checkpoint_rows == 0


def _observation_values(
    parsed: ParsedRow,
    *,
    ingestion_run_id: uuid.UUID,
    source_row_number: int,
) -> dict[str, Any]:
    return {
        "ingestion_run_id": ingestion_run_id,
        "source_row_number": source_row_number,
        "series_reference": parsed.series_reference,
        "period": parsed.period,
        "data_value": parsed.data_value,
        "status": parsed.status,
        "units": parsed.units,
        "magnitude": parsed.magnitude,
        "subject": parsed.subject,
        "group": parsed.group,
        "series_title_1": parsed.series_title_1,
        "series_title_2": parsed.series_title_2,
        "series_title_3": parsed.series_title_3,
        "series_title_4": parsed.series_title_4,
        "series_title_5": parsed.series_title_5,
    }


def process_validation_profile(
    session: Session,
    *,
    run: IngestionRun,
    task: RunTask,
    s3_client: Any,
    checkpoint_rows: int,
    bucket_name: str,
) -> int:
    counts = ValidationCounts()
    with stream_csv_rows(
        s3_client, bucket_name=bucket_name, object_key=run.s3_key
    ) as (reader, bytes_read, content_length):
        fieldnames = reader.fieldnames
        for row in reader:
            validate_row(row, counts)
            if _checkpoint_due(counts.row_count, checkpoint_rows):
                checkpoint_task(
                    session,
                    run_task_id=task.id,
                    processed_rows=counts.row_count,
                    progress_percent=progress_from_bytes(bytes_read(), content_length),
                )
        upsert_validation_profile(
            session,
            ingestion_run_id=run.id,
            counts=counts,
            fieldnames=fieldnames,
        )
        checkpoint_task(
            session,
            run_task_id=task.id,
            processed_rows=counts.row_count,
            progress_percent=progress_from_bytes(bytes_read(), content_length),
        )
    return counts.row_count


def process_observations(
    session: Session,
    *,
    run: IngestionRun,
    task: RunTask,
    s3_client: Any,
    checkpoint_rows: int,
    bucket_name: str,
) -> int:
    rows: list[dict[str, Any]] = []
    processed_rows = 0
    with stream_csv_rows(
        s3_client, bucket_name=bucket_name, object_key=run.s3_key
    ) as (reader, bytes_read, content_length):
        require_expected_header(reader.fieldnames)
        for source_row_number, row in enumerate(reader, start=2):
            processed_rows += 1
            parsed = parse_valid_row(row)
            if parsed is not None:
                rows.append(
                    _observation_values(
                        parsed,
                        ingestion_run_id=run.id,
                        source_row_number=source_row_number,
                    )
                )
            if _checkpoint_due(processed_rows, checkpoint_rows):
                upsert_observations(session, rows)
                rows.clear()
                checkpoint_task(
                    session,
                    run_task_id=task.id,
                    processed_rows=processed_rows,
                    progress_percent=progress_from_bytes(bytes_read(), content_length),
                )
        upsert_observations(session, rows)
        checkpoint_task(
            session,
            run_task_id=task.id,
            processed_rows=processed_rows,
            progress_percent=progress_from_bytes(bytes_read(), content_length),
        )
    return processed_rows


def _summary_values(
    *, ingestion_run_id: uuid.UUID, series_reference: str, accumulator: SeriesAccumulator
) -> dict[str, Any]:
    ordered = sorted(accumulator.values, key=lambda value: value[0])
    first_period, first_value = ordered[0] if ordered else (None, None)
    latest_period, latest_value = ordered[-1] if ordered else (None, None)
    qoq_change: Decimal | None = None
    if len(ordered) >= 2:
        previous_period, previous_value = ordered[-2]
        if (latest_period.year - previous_period.year) * 12 + latest_period.month - previous_period.month == 3:
            qoq_change = latest_value - previous_value
    values = [value for _, value in ordered]
    return {
        "ingestion_run_id": ingestion_run_id,
        "series_reference": series_reference,
        "units": accumulator.units,
        "valid_observation_count": len(ordered),
        "first_period": first_period,
        "first_value": first_value,
        "latest_period": latest_period,
        "latest_value": latest_value,
        "min_value": min(values) if values else None,
        "max_value": max(values) if values else None,
        "quarter_to_quarter_change": qoq_change,
    }


def process_series_summaries(
    session: Session,
    *,
    run: IngestionRun,
    task: RunTask,
    s3_client: Any,
    checkpoint_rows: int,
    bucket_name: str,
) -> int:
    series: dict[str, SeriesAccumulator] = {}
    processed_rows = 0
    with stream_csv_rows(
        s3_client, bucket_name=bucket_name, object_key=run.s3_key
    ) as (reader, bytes_read, content_length):
        require_expected_header(reader.fieldnames)
        for row in reader:
            processed_rows += 1
            parsed = parse_valid_row(row)
            if parsed is not None:
                accumulator = series.setdefault(
                    parsed.series_reference, SeriesAccumulator(units=parsed.units)
                )
                # A summary row has one units column. Do not combine values from
                # inconsistent units under a single series reference.
                if accumulator.units == parsed.units:
                    accumulator.add(parsed)
            if _checkpoint_due(processed_rows, checkpoint_rows):
                checkpoint_task(
                    session,
                    run_task_id=task.id,
                    processed_rows=processed_rows,
                    progress_percent=progress_from_bytes(bytes_read(), content_length),
                )
        upsert_series_summaries(
            session,
            [
                _summary_values(
                    ingestion_run_id=run.id,
                    series_reference=series_reference,
                    accumulator=accumulator,
                )
                for series_reference, accumulator in series.items()
            ],
        )
        checkpoint_task(
            session,
            run_task_id=task.id,
            processed_rows=processed_rows,
            progress_percent=progress_from_bytes(bytes_read(), content_length),
        )
    return processed_rows


Processor = Callable[..., int]


def _run_task(
    celery_task: Task,
    *,
    ingestion_run_id: str,
    run_task_id: str,
    task_type: RunTaskType,
    processor: Processor,
) -> dict[str, Any]:
    settings = get_settings()
    parsed_run_id = uuid.UUID(ingestion_run_id)
    parsed_task_id = uuid.UUID(run_task_id)
    session = SessionLocal()
    try:
        started = _start_task(
            session,
            ingestion_run_id=parsed_run_id,
            run_task_id=parsed_task_id,
            task_type=task_type,
        )
        if started is None:
            return {"status": "already_terminal", "run_task_id": run_task_id}
        run, task = started
        processed_rows = processor(
            session,
            run=run,
            task=task,
            s3_client=get_s3_client(),
            checkpoint_rows=settings.worker_checkpoint_rows,
            bucket_name=settings.s3_upload_bucket,
        )
        _finish_task(session, run_task_id=task.id, processed_rows=processed_rows)
        return {"status": "succeeded", "run_task_id": run_task_id}
    except RETRIABLE_EXCEPTIONS as exc:
        session.rollback()
        retries = celery_task.request.retries
        if retries >= settings.worker_max_retries:
            _fail_task(session, run_task_id=parsed_task_id, exception=exc)
            raise
        _mark_retry(session, run_task_id=parsed_task_id, exception=exc)
        raise celery_task.retry(
            exc=exc,
            countdown=min(60, 2 ** retries),
            max_retries=settings.worker_max_retries,
        ) from exc
    except Exception as exc:
        session.rollback()
        _fail_task(session, run_task_id=parsed_task_id, exception=exc)
        raise
    finally:
        session.close()


@celery_app.task(bind=True, name="app.workers.tasks.validate_profile")
def validate_profile(
    self: Task, *, ingestion_run_id: str, run_task_id: str
) -> dict[str, Any]:
    return _run_task(
        self,
        ingestion_run_id=ingestion_run_id,
        run_task_id=run_task_id,
        task_type=RunTaskType.VALIDATE_PROFILE,
        processor=process_validation_profile,
    )


@celery_app.task(bind=True, name="app.workers.tasks.load_observations")
def load_observations(
    self: Task, *, ingestion_run_id: str, run_task_id: str
) -> dict[str, Any]:
    return _run_task(
        self,
        ingestion_run_id=ingestion_run_id,
        run_task_id=run_task_id,
        task_type=RunTaskType.LOAD_OBSERVATIONS,
        processor=process_observations,
    )


@celery_app.task(bind=True, name="app.workers.tasks.build_series_summaries")
def build_series_summaries(
    self: Task, *, ingestion_run_id: str, run_task_id: str
) -> dict[str, Any]:
    return _run_task(
        self,
        ingestion_run_id=ingestion_run_id,
        run_task_id=run_task_id,
        task_type=RunTaskType.BUILD_SERIES_SUMMARIES,
        processor=process_series_summaries,
    )
