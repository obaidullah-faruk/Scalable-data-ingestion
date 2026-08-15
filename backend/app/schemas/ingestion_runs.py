import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import RunStatus, RunTaskStatus, RunTaskType


ALLOWED_CSV_CONTENT_TYPES = {
    "application/csv",
    "application/vnd.ms-excel",
    "text/csv",
}


def validate_etag_value(etag: str) -> str:
    normalized = etag.strip()
    if not normalized or any(ord(character) < 32 for character in normalized):
        raise ValueError("etag must not be blank or contain control characters")
    return normalized


class CreateIngestionRunRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    filename: str = Field(min_length=1, max_length=1024)
    content_type: str = Field(min_length=1, max_length=255)
    byte_size: int = Field(gt=0)

    @field_validator("filename")
    @classmethod
    def validate_csv_filename(cls, filename: str) -> str:
        if "/" in filename or "\\" in filename or "\x00" in filename:
            raise ValueError("filename must not contain a path")
        if not filename.lower().endswith(".csv"):
            raise ValueError("filename must end with .csv")
        return filename

    @field_validator("content_type")
    @classmethod
    def validate_csv_content_type(cls, content_type: str) -> str:
        normalized = content_type.split(";", 1)[0].strip().lower()
        if normalized not in ALLOWED_CSV_CONTENT_TYPES:
            raise ValueError("content_type must identify CSV data")
        return normalized


class CreateIngestionRunResponse(BaseModel):
    run_id: uuid.UUID
    status: RunStatus
    object_key: str
    upload_id: str
    part_size_bytes: int
    total_parts: int
    part_url_batch_limit: int
    part_urls_endpoint: str


class PartUrlsRequest(BaseModel):
    part_numbers: list[int] = Field(min_length=1)

    @field_validator("part_numbers")
    @classmethod
    def validate_part_numbers(cls, part_numbers: list[int]) -> list[int]:
        if len(set(part_numbers)) != len(part_numbers):
            raise ValueError("part_numbers must be unique")
        if any(number < 1 or number > 10_000 for number in part_numbers):
            raise ValueError("part numbers must be between 1 and 10000")
        return part_numbers


class PresignedPartUrl(BaseModel):
    part_number: int
    url: str


class PartUrlsResponse(BaseModel):
    run_id: uuid.UUID
    expires_in_seconds: int
    parts: list[PresignedPartUrl]


class AbortIngestionRunResponse(BaseModel):
    run_id: uuid.UUID
    status: RunStatus


class CompletedPart(BaseModel):
    part_number: int = Field(ge=1, le=10_000)
    etag: str = Field(min_length=1, max_length=1024)

    @field_validator("etag")
    @classmethod
    def validate_etag(cls, etag: str) -> str:
        return validate_etag_value(etag)


class CompletionRequest(BaseModel):
    upload_id: str = Field(min_length=1, max_length=1024)
    parts: list[CompletedPart] = Field(min_length=1, max_length=10_000)


class SignedCompletionRequest(BaseModel):
    method: str
    url: str
    headers: dict[str, str]
    body: str


class ConfirmUploadRequest(BaseModel):
    object_etag: str = Field(min_length=1, max_length=1024)
    object_version_id: str | None = Field(default=None, max_length=1024)

    @field_validator("object_etag")
    @classmethod
    def validate_object_etag(cls, object_etag: str) -> str:
        return validate_etag_value(object_etag)


class RunTaskResponse(BaseModel):
    task_type: RunTaskType
    status: RunTaskStatus
    celery_task_id: str | None


class ConfirmUploadResponse(BaseModel):
    run_id: uuid.UUID
    status: RunStatus
    object_etag: str
    object_version_id: str | None
    tasks: list[RunTaskResponse]


class RunTaskSnapshotResponse(BaseModel):
    task_id: uuid.UUID
    task_type: RunTaskType
    status: RunTaskStatus
    progress_percent: int
    processed_rows: int
    retry_count: int
    celery_task_id: str | None
    error_details: dict | None
    started_at: datetime | None
    completed_at: datetime | None


class ValidationProfileResponse(BaseModel):
    row_count: int
    missing_data_value_count: int
    invalid_period_count: int
    invalid_data_value_count: int
    invalid_status_count: int
    invalid_units_count: int
    findings: dict


class SeriesSummaryResponse(BaseModel):
    series_reference: str
    units: str | None
    valid_observation_count: int
    first_period: str | None
    first_value: Decimal | None
    latest_period: str | None
    latest_value: Decimal | None
    min_value: Decimal | None
    max_value: Decimal | None
    quarter_to_quarter_change: Decimal | None


class IngestionRunSnapshotResponse(BaseModel):
    run_id: uuid.UUID
    status: RunStatus
    original_filename: str
    size_bytes: int
    uploaded_bytes: int
    processing_progress_percent: int
    completed_task_count: int
    total_task_count: int
    error_details: dict | None
    upload_confirmed_at: datetime | None
    processing_started_at: datetime | None
    completed_at: datetime | None
    tasks: list[RunTaskSnapshotResponse]
    validation_profile: ValidationProfileResponse | None
    series_summaries: list[SeriesSummaryResponse]
