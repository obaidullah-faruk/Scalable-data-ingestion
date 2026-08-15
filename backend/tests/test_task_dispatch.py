from types import SimpleNamespace
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, IngestionRun, RunStatus, RunTask, RunTaskStatus, RunTaskType
from app.services import task_dispatch


def test_reconciliation_dispatches_only_queued_tasks_without_celery_ids(monkeypatch) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    run_id = uuid.uuid4()
    with session_factory() as session:
        session.add(
            IngestionRun(
                id=run_id,
                status=RunStatus.QUEUED,
                original_filename="gdp.csv",
                s3_key=f"uploads/{run_id}/source.csv",
                size_bytes=1,
            )
        )
        other_run_id = uuid.uuid4()
        session.add(
            IngestionRun(
                id=other_run_id,
                status=RunStatus.PROCESSING,
                original_filename="other.csv",
                s3_key=f"uploads/{other_run_id}/source.csv",
                size_bytes=1,
            )
        )
        session.add_all(
            [
                RunTask(ingestion_run_id=run_id, task_type=task_type)
                for task_type in RunTaskType
            ]
        )
        session.add(
            RunTask(
                ingestion_run_id=other_run_id,
                task_type=RunTaskType.VALIDATE_PROFILE,
                status=RunTaskStatus.PROCESSING,
            )
        )
        session.commit()

        published: list[tuple[str, dict[str, str]]] = []

        def send_task(task_name: str, *, kwargs: dict[str, str]):
            published.append((task_name, kwargs))
            return SimpleNamespace(id=f"celery-{len(published)}")

        monkeypatch.setattr(task_dispatch.celery_app, "send_task", send_task)

        assert task_dispatch.reconcile_queued_tasks(session) == 3
        assert task_dispatch.reconcile_queued_tasks(session) == 0
        assert len(published) == 3
        assert {task_name for task_name, _ in published} == set(
            task_dispatch.CELERY_TASK_NAMES.values()
        )
        task_rows = session.query(RunTask).filter_by(ingestion_run_id=run_id).all()
        assert {task.celery_task_id for task in task_rows} == {
            "celery-1",
            "celery-2",
            "celery-3",
        }
