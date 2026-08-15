"""create Phase 3 ingestion schema

Revision ID: 20260815_0001
Revises:
Create Date: 2026-08-15 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260815_0001"
down_revision = None
branch_labels = None
depends_on = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("original_filename", sa.String(1024), nullable=False),
        sa.Column("s3_key", sa.String(2048), nullable=False),
        sa.Column("upload_id", sa.String(1024)),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("uploaded_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("processing_progress_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_task_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_task_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_details", sa.JSON()),
        sa.Column("upload_confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("processing_started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.CheckConstraint("size_bytes >= 0", name="ck_ingestion_runs_size_nonnegative"),
        sa.CheckConstraint("uploaded_bytes >= 0 AND uploaded_bytes <= size_bytes", name="ck_ingestion_runs_uploaded_bytes_range"),
        sa.CheckConstraint("processing_progress_percent BETWEEN 0 AND 100", name="ck_ingestion_runs_progress_range"),
        sa.CheckConstraint("status IN ('UPLOADING', 'AWAITING_CONFIRMATION', 'QUEUED', 'PROCESSING', 'SUCCEEDED', 'PARTIALLY_FAILED', 'FAILED')", name="ck_ingestion_runs_status"),
        sa.UniqueConstraint("s3_key", name="uq_ingestion_runs_s3_key"),
        sa.UniqueConstraint("upload_id", name="uq_ingestion_runs_upload_id"),
    )
    op.create_table(
        "run_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("ingestion_run_id", sa.Uuid(), sa.ForeignKey("ingestion_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_rows", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("celery_task_id", sa.String(255)),
        sa.Column("error_details", sa.JSON()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.CheckConstraint("progress_percent BETWEEN 0 AND 100", name="ck_run_tasks_progress_range"),
        sa.CheckConstraint("processed_rows >= 0", name="ck_run_tasks_processed_rows_nonnegative"),
        sa.CheckConstraint("retry_count >= 0", name="ck_run_tasks_retry_count_nonnegative"),
        sa.CheckConstraint("task_type IN ('VALIDATE_PROFILE', 'LOAD_OBSERVATIONS', 'BUILD_SERIES_SUMMARIES')", name="ck_run_tasks_type"),
        sa.CheckConstraint("status IN ('QUEUED', 'PROCESSING', 'SUCCEEDED', 'FAILED')", name="ck_run_tasks_status"),
        sa.UniqueConstraint("ingestion_run_id", "task_type", name="uq_run_tasks_run_task_type"),
        sa.UniqueConstraint("celery_task_id", name="uq_run_tasks_celery_task_id"),
    )
    op.create_index("ix_run_tasks_ingestion_run_id", "run_tasks", ["ingestion_run_id"])
    op.create_table(
        "run_validation_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("ingestion_run_id", sa.Uuid(), sa.ForeignKey("ingestion_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("missing_data_value_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("invalid_period_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("invalid_data_value_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("invalid_status_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("invalid_units_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("findings", sa.JSON(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("ingestion_run_id", name="uq_run_validation_profiles_run"),
    )
    op.create_table(
        "gdp_observations",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("ingestion_run_id", sa.Uuid(), sa.ForeignKey("ingestion_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_row_number", sa.BigInteger(), nullable=False),
        sa.Column("series_reference", sa.String(255), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("data_value", sa.Numeric(24, 8)),
        sa.Column("status", sa.String(32)),
        sa.Column("units", sa.String(255)),
        sa.Column("magnitude", sa.String(255)),
        sa.Column("subject", sa.Text()),
        sa.Column("group", sa.Text()),
        sa.Column("series_title_1", sa.Text()),
        sa.Column("series_title_2", sa.Text()),
        sa.Column("series_title_3", sa.Text()),
        sa.Column("series_title_4", sa.Text()),
        sa.Column("series_title_5", sa.Text()),
        *timestamps(),
        sa.UniqueConstraint("ingestion_run_id", "source_row_number", name="uq_gdp_observations_run_source_row"),
    )
    op.create_index("ix_gdp_observations_run_series_period", "gdp_observations", ["ingestion_run_id", "series_reference", "period"])
    op.create_table(
        "gdp_series_summaries",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("ingestion_run_id", sa.Uuid(), sa.ForeignKey("ingestion_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("series_reference", sa.String(255), nullable=False),
        sa.Column("units", sa.String(255)),
        sa.Column("valid_observation_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("first_period", sa.Date()),
        sa.Column("first_value", sa.Numeric(24, 8)),
        sa.Column("latest_period", sa.Date()),
        sa.Column("latest_value", sa.Numeric(24, 8)),
        sa.Column("min_value", sa.Numeric(24, 8)),
        sa.Column("max_value", sa.Numeric(24, 8)),
        sa.Column("quarter_to_quarter_change", sa.Numeric(24, 8)),
        *timestamps(),
        sa.UniqueConstraint("ingestion_run_id", "series_reference", name="uq_gdp_series_summaries_run_series"),
    )
    op.create_index("ix_gdp_series_summaries_run_id", "gdp_series_summaries", ["ingestion_run_id"])


def downgrade() -> None:
    op.drop_index("ix_gdp_series_summaries_run_id", table_name="gdp_series_summaries")
    op.drop_table("gdp_series_summaries")
    op.drop_index("ix_gdp_observations_run_series_period", table_name="gdp_observations")
    op.drop_table("gdp_observations")
    op.drop_table("run_validation_profiles")
    op.drop_index("ix_run_tasks_ingestion_run_id", table_name="run_tasks")
    op.drop_table("run_tasks")
    op.drop_table("ingestion_runs")
