"""Read durable ingestion-run snapshots for HTTP and SSE consumers."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import IngestionRun
from app.schemas.ingestion_runs import (
    IngestionRunSnapshotResponse,
    RunTaskSnapshotResponse,
    SeriesSummaryResponse,
    ValidationProfileResponse,
)


TERMINAL_RUN_STATUSES = {"SUCCEEDED", "PARTIALLY_FAILED", "FAILED"}


def is_terminal_run_status(status: str) -> bool:
    return status in TERMINAL_RUN_STATUSES


def get_run_snapshot(
    session: Session, run_id: uuid.UUID
) -> IngestionRunSnapshotResponse | None:
    run = session.scalar(
        select(IngestionRun)
        .where(IngestionRun.id == run_id)
        .options(
            selectinload(IngestionRun.tasks),
            selectinload(IngestionRun.validation_profile),
            selectinload(IngestionRun.series_summaries),
        )
    )
    if run is None:
        return None

    profile = run.validation_profile
    return IngestionRunSnapshotResponse(
        run_id=run.id,
        status=run.status,
        original_filename=run.original_filename,
        size_bytes=run.size_bytes,
        uploaded_bytes=run.uploaded_bytes,
        processing_progress_percent=run.processing_progress_percent,
        completed_task_count=run.completed_task_count,
        total_task_count=run.total_task_count,
        error_details=run.error_details,
        upload_confirmed_at=run.upload_confirmed_at,
        processing_started_at=run.processing_started_at,
        completed_at=run.completed_at,
        tasks=[
            RunTaskSnapshotResponse(
                task_id=task.id,
                task_type=task.task_type,
                status=task.status,
                progress_percent=task.progress_percent,
                processed_rows=task.processed_rows,
                retry_count=task.retry_count,
                celery_task_id=task.celery_task_id,
                error_details=task.error_details,
                started_at=task.started_at,
                completed_at=task.completed_at,
            )
            for task in sorted(run.tasks, key=lambda task: task.task_type.value)
        ],
        validation_profile=(
            ValidationProfileResponse(
                row_count=profile.row_count,
                missing_data_value_count=profile.missing_data_value_count,
                invalid_period_count=profile.invalid_period_count,
                invalid_data_value_count=profile.invalid_data_value_count,
                invalid_status_count=profile.invalid_status_count,
                invalid_units_count=profile.invalid_units_count,
                findings=profile.findings,
            )
            if profile is not None
            else None
        ),
        series_summaries=[
            SeriesSummaryResponse(
                series_reference=summary.series_reference,
                units=summary.units,
                valid_observation_count=summary.valid_observation_count,
                first_period=(summary.first_period.isoformat() if summary.first_period else None),
                first_value=summary.first_value,
                latest_period=(summary.latest_period.isoformat() if summary.latest_period else None),
                latest_value=summary.latest_value,
                min_value=summary.min_value,
                max_value=summary.max_value,
                quarter_to_quarter_change=summary.quarter_to_quarter_change,
            )
            for summary in sorted(
                run.series_summaries, key=lambda summary: summary.series_reference
            )
        ],
    )
