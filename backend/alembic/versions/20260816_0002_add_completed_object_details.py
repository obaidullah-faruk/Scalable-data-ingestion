"""add completed object details to ingestion runs

Revision ID: 20260816_0002
Revises: 20260815_0001
Create Date: 2026-08-16 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260816_0002"
down_revision = "20260815_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ingestion_runs", sa.Column("object_etag", sa.String(1024)))
    op.add_column(
        "ingestion_runs",
        sa.Column("object_version_id", sa.String(1024)),
    )


def downgrade() -> None:
    op.drop_column("ingestion_runs", "object_version_id")
    op.drop_column("ingestion_runs", "object_etag")
