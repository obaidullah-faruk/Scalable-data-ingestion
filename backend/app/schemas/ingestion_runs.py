import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import RunStatus


ALLOWED_CSV_CONTENT_TYPES = {
    "application/csv",
    "application/vnd.ms-excel",
    "text/csv",
}


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
