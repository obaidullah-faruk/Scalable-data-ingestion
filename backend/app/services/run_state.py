from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.ingestion import IngestionRun, RunStatus, RunTask, RunTaskStatus


TERMINAL_TASK_STATUSES = {RunTaskStatus.SUCCEEDED, RunTaskStatus.FAILED}


def derive_run_state(run: IngestionRun, tasks: list[RunTask] | None = None) -> IngestionRun:
    """Update durable parent progress and state solely from its child task rows.

    Runs without tasks remain in their upload lifecycle; task lifecycle starts when
    Phase 8 creates the three child rows.
    """
    task_rows = tasks if tasks is not None else list(run.tasks)
    if not task_rows:
        return run

    total = len(task_rows)
    completed = sum(task.status in TERMINAL_TASK_STATUSES for task in task_rows)
    progress = round(sum(100 if task.status == RunTaskStatus.SUCCEEDED else task.progress_percent for task in task_rows) / total)
    failed = sum(task.status == RunTaskStatus.FAILED for task in task_rows)

    run.total_task_count = total
    run.completed_task_count = completed
    run.processing_progress_percent = max(0, min(100, progress))

    if completed == total:
        run.status = RunStatus.SUCCEEDED if failed == 0 else (RunStatus.FAILED if failed == total else RunStatus.PARTIALLY_FAILED)
        run.completed_at = run.completed_at or datetime.now(UTC)
    elif any(task.status == RunTaskStatus.PROCESSING for task in task_rows) or completed:
        run.status = RunStatus.PROCESSING
        run.processing_started_at = run.processing_started_at or datetime.now(UTC)
    else:
        run.status = RunStatus.QUEUED
    return run


def refresh_run_state(session: Session, run_id: object) -> IngestionRun:
    run = session.get(IngestionRun, run_id)
    if run is None:
        raise ValueError(f"Ingestion run {run_id} does not exist")
    return derive_run_state(run)
