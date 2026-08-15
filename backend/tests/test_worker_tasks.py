from io import BytesIO
from types import SimpleNamespace
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base,
    GdpObservation,
    GdpSeriesSummary,
    IngestionRun,
    RunStatus,
    RunTask,
    RunTaskStatus,
    RunTaskType,
    RunValidationProfile,
)
from app.workers import tasks


CSV = """Series_reference,Period,Data_value,STATUS,UNITS,MAGNITUDE,Subject,Group,Series_title_1,Series_title_2,Series_title_3,Series_title_4,Series_title_5
A,1972.03,10,FINAL,Dollars,6,Subject,Group,Title,,,,
A,1972.06,15,FINAL,Dollars,6,Subject,Group,Title,,,,
B,1972.03,,FINAL,Index,0,Subject,Group,Title,,,,
B,not-a-period,3,FINAL,Index,0,Subject,Group,Title,,,,
C,1972.03,not-a-number,,,0,Subject,Group,Title,,,,
""".encode()


class FakeS3Client:
    def get_object(self, *, Bucket: str, Key: str):
        return {"Body": BytesIO(CSV), "ContentLength": len(CSV)}


class FakeCeleryTask:
    request = SimpleNamespace(retries=0)

    def retry(self, **kwargs):  # pragma: no cover - no retry is expected here
        raise AssertionError(f"unexpected retry: {kwargs}")


def test_workers_stream_checkpoint_and_upsert_results(monkeypatch) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    run_id = uuid.uuid4()

    with session_factory() as session:
        run = IngestionRun(
            id=run_id,
            status=RunStatus.QUEUED,
            original_filename="source.csv",
            s3_key=f"uploads/{run_id}/source.csv",
            size_bytes=len(CSV),
        )
        session.add(run)
        session.flush()
        task_rows = {
            task_type: RunTask(ingestion_run_id=run.id, task_type=task_type)
            for task_type in RunTaskType
        }
        session.add_all(task_rows.values())
        session.commit()
        task_ids = {task_type: task.id for task_type, task in task_rows.items()}

    monkeypatch.setattr(tasks, "SessionLocal", session_factory)
    monkeypatch.setattr(tasks, "get_s3_client", lambda: FakeS3Client())
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(
            worker_checkpoint_rows=2,
            worker_max_retries=3,
            s3_upload_bucket="csv-ingestion-uploads",
        ),
    )
    events: list[dict] = []
    monkeypatch.setattr(tasks, "publish_progress_event", events.append)

    fake_celery_task = FakeCeleryTask()
    for task_type, processor in (
        (RunTaskType.VALIDATE_PROFILE, tasks.process_validation_profile),
        (RunTaskType.LOAD_OBSERVATIONS, tasks.process_observations),
        (RunTaskType.BUILD_SERIES_SUMMARIES, tasks.process_series_summaries),
    ):
        assert tasks._run_task(
            fake_celery_task,
            ingestion_run_id=str(run_id),
            run_task_id=str(task_ids[task_type]),
            task_type=task_type,
            processor=processor,
        )["status"] == "succeeded"

    # A redelivered Celery message does not rerun a terminal task.
    assert tasks._run_task(
        fake_celery_task,
        ingestion_run_id=str(run_id),
        run_task_id=str(task_ids[RunTaskType.LOAD_OBSERVATIONS]),
        task_type=RunTaskType.LOAD_OBSERVATIONS,
        processor=tasks.process_observations,
    )["status"] == "already_terminal"

    # Simulate a restart after result chunks were committed but before the
    # worker recorded terminal state. Replaying each stream must upsert.
    with session_factory() as session:
        run = session.get(IngestionRun, run_id)
        observation_task = session.get(
            RunTask, task_ids[RunTaskType.LOAD_OBSERVATIONS]
        )
        summary_task = session.get(
            RunTask, task_ids[RunTaskType.BUILD_SERIES_SUMMARIES]
        )
        observation_task.status = RunTaskStatus.PROCESSING
        observation_task.completed_at = None
        summary_task.status = RunTaskStatus.PROCESSING
        summary_task.completed_at = None
        session.commit()

        observation_rows = tasks.process_observations(
            session,
            run=run,
            task=observation_task,
            s3_client=FakeS3Client(),
            checkpoint_rows=2,
            bucket_name="csv-ingestion-uploads",
        )
        tasks._finish_task(
            session,
            run_task_id=observation_task.id,
            processed_rows=observation_rows,
        )
        summary_rows = tasks.process_series_summaries(
            session,
            run=run,
            task=summary_task,
            s3_client=FakeS3Client(),
            checkpoint_rows=2,
            bucket_name="csv-ingestion-uploads",
        )
        tasks._finish_task(
            session,
            run_task_id=summary_task.id,
            processed_rows=summary_rows,
        )

    with session_factory() as session:
        run = session.get(IngestionRun, run_id)
        validation = session.query(RunValidationProfile).filter_by(ingestion_run_id=run_id).one()
        observations = session.query(GdpObservation).filter_by(ingestion_run_id=run_id).all()
        summaries = session.query(GdpSeriesSummary).filter_by(ingestion_run_id=run_id).all()
        task_rows = session.query(RunTask).filter_by(ingestion_run_id=run_id).all()

        assert validation.row_count == 5
        assert validation.missing_data_value_count == 1
        assert validation.invalid_period_count == 1
        assert validation.invalid_data_value_count == 1
        assert validation.invalid_status_count == 1
        assert validation.invalid_units_count == 1
        assert len(observations) == 3
        assert {summary.series_reference for summary in summaries} == {"A", "B"}
        a_summary = next(summary for summary in summaries if summary.series_reference == "A")
        assert a_summary.valid_observation_count == 2
        assert a_summary.quarter_to_quarter_change == 5
        assert run.status == RunStatus.SUCCEEDED
        assert run.processing_progress_percent == 100
        assert all(task.status == RunTaskStatus.SUCCEEDED for task in task_rows)
        assert all(task.processed_rows == 5 for task in task_rows)
        assert events
