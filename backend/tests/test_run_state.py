import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Base, IngestionRun, RunStatus, RunTask, RunTaskStatus, RunTaskType
from app.services.run_state import derive_run_state


def make_run() -> IngestionRun:
    return IngestionRun(original_filename="source.csv", s3_key=f"uploads/{uuid.uuid4()}/source.csv", size_bytes=100)


def test_duplicate_child_task_type_is_rejected() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        run = make_run()
        session.add(run)
        session.flush()
        session.add_all([
            RunTask(ingestion_run_id=run.id, task_type=RunTaskType.VALIDATE_PROFILE),
            RunTask(ingestion_run_id=run.id, task_type=RunTaskType.VALIDATE_PROFILE),
        ])
        with pytest.raises(IntegrityError):
            session.flush()


@pytest.mark.parametrize(
    ("task_statuses", "expected"),
    [
        ([RunTaskStatus.QUEUED] * 3, RunStatus.QUEUED),
        ([RunTaskStatus.PROCESSING, RunTaskStatus.QUEUED, RunTaskStatus.QUEUED], RunStatus.PROCESSING),
        ([RunTaskStatus.SUCCEEDED, RunTaskStatus.PROCESSING, RunTaskStatus.QUEUED], RunStatus.PROCESSING),
        ([RunTaskStatus.SUCCEEDED, RunTaskStatus.SUCCEEDED, RunTaskStatus.SUCCEEDED], RunStatus.SUCCEEDED),
        ([RunTaskStatus.SUCCEEDED, RunTaskStatus.FAILED, RunTaskStatus.SUCCEEDED], RunStatus.PARTIALLY_FAILED),
        ([RunTaskStatus.FAILED] * 3, RunStatus.FAILED),
    ],
)
def test_parent_state_is_derived_from_child_tasks(task_statuses: list[RunTaskStatus], expected: RunStatus) -> None:
    run = make_run()
    task_types = list(RunTaskType)
    tasks = [RunTask(task_type=task_types[index], status=status, progress_percent=30) for index, status in enumerate(task_statuses)]

    derive_run_state(run, tasks)

    assert run.status == expected
    assert run.total_task_count == 3
    assert run.completed_task_count == sum(status in {RunTaskStatus.SUCCEEDED, RunTaskStatus.FAILED} for status in task_statuses)
