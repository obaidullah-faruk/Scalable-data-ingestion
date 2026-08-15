import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, Enum, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid


class Base(DeclarativeBase):
    pass


class RunStatus(str, enum.Enum):
    UPLOADING = "UPLOADING"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    PARTIALLY_FAILED = "PARTIALLY_FAILED"
    FAILED = "FAILED"


class RunTaskType(str, enum.Enum):
    VALIDATE_PROFILE = "VALIDATE_PROFILE"
    LOAD_OBSERVATIONS = "LOAD_OBSERVATIONS"
    BUILD_SERIES_SUMMARIES = "BUILD_SERIES_SUMMARIES"


class RunTaskStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class IngestionRun(Base, Timestamped):
    __tablename__ = "ingestion_runs"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_ingestion_runs_size_nonnegative"),
        CheckConstraint("uploaded_bytes >= 0 AND uploaded_bytes <= size_bytes", name="ck_ingestion_runs_uploaded_bytes_range"),
        CheckConstraint("processing_progress_percent BETWEEN 0 AND 100", name="ck_ingestion_runs_progress_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus, native_enum=False, length=32), default=RunStatus.UPLOADING, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    upload_id: Mapped[str | None] = mapped_column(String(1024), unique=True)
    object_etag: Mapped[str | None] = mapped_column(String(1024))
    object_version_id: Mapped[str | None] = mapped_column(String(1024))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uploaded_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    processing_progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_task_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_task_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_details: Mapped[dict | None] = mapped_column(JSON)
    upload_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tasks: Mapped[list["RunTask"]] = relationship(back_populates="ingestion_run", cascade="all, delete-orphan")
    validation_profile: Mapped["RunValidationProfile | None"] = relationship(back_populates="ingestion_run", cascade="all, delete-orphan")
    observations: Mapped[list["GdpObservation"]] = relationship(back_populates="ingestion_run", cascade="all, delete-orphan")
    series_summaries: Mapped[list["GdpSeriesSummary"]] = relationship(back_populates="ingestion_run", cascade="all, delete-orphan")


class RunTask(Base, Timestamped):
    __tablename__ = "run_tasks"
    __table_args__ = (
        UniqueConstraint("ingestion_run_id", "task_type", name="uq_run_tasks_run_task_type"),
        CheckConstraint("progress_percent BETWEEN 0 AND 100", name="ck_run_tasks_progress_range"),
        CheckConstraint("processed_rows >= 0", name="ck_run_tasks_processed_rows_nonnegative"),
        CheckConstraint("retry_count >= 0", name="ck_run_tasks_retry_count_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ingestion_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("ingestion_runs.id", ondelete="CASCADE"), nullable=False)
    task_type: Mapped[RunTaskType] = mapped_column(Enum(RunTaskType, native_enum=False, length=40), nullable=False)
    status: Mapped[RunTaskStatus] = mapped_column(Enum(RunTaskStatus, native_enum=False, length=16), default=RunTaskStatus.QUEUED, nullable=False)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_rows: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    error_details: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ingestion_run: Mapped[IngestionRun] = relationship(back_populates="tasks")


class RunValidationProfile(Base, Timestamped):
    __tablename__ = "run_validation_profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ingestion_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("ingestion_runs.id", ondelete="CASCADE"), unique=True, nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    missing_data_value_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    invalid_period_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    invalid_data_value_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    invalid_status_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    invalid_units_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    findings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    ingestion_run: Mapped[IngestionRun] = relationship(back_populates="validation_profile")


class GdpObservation(Base, Timestamped):
    __tablename__ = "gdp_observations"
    __table_args__ = (UniqueConstraint("ingestion_run_id", "source_row_number", name="uq_gdp_observations_run_source_row"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ingestion_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("ingestion_runs.id", ondelete="CASCADE"), nullable=False)
    source_row_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    series_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    period: Mapped[date] = mapped_column(Date, nullable=False)
    data_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    status: Mapped[str | None] = mapped_column("status", String(32))
    units: Mapped[str | None] = mapped_column(String(255))
    magnitude: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(Text)
    group: Mapped[str | None] = mapped_column("group", Text)
    series_title_1: Mapped[str | None] = mapped_column(Text)
    series_title_2: Mapped[str | None] = mapped_column(Text)
    series_title_3: Mapped[str | None] = mapped_column(Text)
    series_title_4: Mapped[str | None] = mapped_column(Text)
    series_title_5: Mapped[str | None] = mapped_column(Text)

    ingestion_run: Mapped[IngestionRun] = relationship(back_populates="observations")


class GdpSeriesSummary(Base, Timestamped):
    __tablename__ = "gdp_series_summaries"
    __table_args__ = (UniqueConstraint("ingestion_run_id", "series_reference", name="uq_gdp_series_summaries_run_series"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ingestion_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("ingestion_runs.id", ondelete="CASCADE"), nullable=False)
    series_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    units: Mapped[str | None] = mapped_column(String(255))
    valid_observation_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    first_period: Mapped[date | None] = mapped_column(Date)
    first_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    latest_period: Mapped[date | None] = mapped_column(Date)
    latest_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    min_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    max_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    quarter_to_quarter_change: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))

    ingestion_run: Mapped[IngestionRun] = relationship(back_populates="series_summaries")
