"""Durable dispatch of ingestion-run Celery tasks.

The database is the source of truth for work that needs to be published. A task
row is created before publication; a missing ``celery_task_id`` therefore means
that it is safe for the reconciliation command to try publication again.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RunTask, RunTaskStatus, RunTaskType
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


CELERY_TASK_NAMES: dict[RunTaskType, str] = {
    RunTaskType.VALIDATE_PROFILE: "app.workers.tasks.validate_profile",
    RunTaskType.LOAD_OBSERVATIONS: "app.workers.tasks.load_observations",
    RunTaskType.BUILD_SERIES_SUMMARIES: "app.workers.tasks.build_series_summaries",
}


def dispatch_queued_tasks(
    session: Session,
    *,
    run_id: uuid.UUID | None = None,
) -> list[RunTask]:
    """Publish queued, undispatched rows and durably retain their Celery IDs.

    Each ID is committed immediately after RabbitMQ accepts the message. If the
    process stops before publication, reconciliation finds the unchanged row.
    If it stops after publication but before the ID commit, a later publication
    is possible; Phase 9 workers use the stable ``run_task_id`` argument and
    durable result-table constraints to make that delivery safe.
    """
    dispatched: list[RunTask] = []

    while True:
        # Lock one row through its Celery-ID commit. This prevents concurrent
        # API/reconciliation processes from publishing the same pending row.
        with session.begin():
            query = select(RunTask).where(
                RunTask.status == RunTaskStatus.QUEUED,
                RunTask.celery_task_id.is_(None),
            )
            if run_id is not None:
                query = query.where(RunTask.ingestion_run_id == run_id)
            task = session.scalars(
                query.order_by(RunTask.created_at, RunTask.id).with_for_update(
                    skip_locked=True
                )
            ).first()
            if task is None:
                break

            result = celery_app.send_task(
                CELERY_TASK_NAMES[task.task_type],
                kwargs={
                    "ingestion_run_id": str(task.ingestion_run_id),
                    "run_task_id": str(task.id),
                },
            )
            task.celery_task_id = result.id
            dispatched.append(task)
            logger.info(
                "Queued ingestion task %s (%s) as Celery task %s",
                task.id,
                task.task_type.value,
                task.celery_task_id,
            )

    return dispatched


def reconcile_queued_tasks(session: Session) -> int:
    """Dispatch every queued task that is missing a Celery task ID."""
    return len(dispatch_queued_tasks(session))
