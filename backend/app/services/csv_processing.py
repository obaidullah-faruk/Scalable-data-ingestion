"""Streaming CSV parsing and idempotent result persistence for ingestion tasks."""

import csv
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
import io
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GdpObservation, GdpSeriesSummary, RunValidationProfile


CSV_COLUMNS = (
    "Series_reference",
    "Period",
    "Data_value",
    "STATUS",
    "UNITS",
    "MAGNITUDE",
    "Subject",
    "Group",
    "Series_title_1",
    "Series_title_2",
    "Series_title_3",
    "Series_title_4",
    "Series_title_5",
)
PERIOD_PATTERN = re.compile(r"^(?P<year>\d{4})\.(?P<month>03|06|09|12)$")


class InvalidCsvHeaderError(ValueError):
    pass


class _CountingBody(io.RawIOBase):
    """Adapt a botocore streaming body to ``TextIOWrapper`` and count bytes."""

    def __init__(self, body: Any) -> None:
        self.body = body
        self.bytes_read = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray) -> int:
        chunk = self.body.read(len(buffer))
        if not chunk:
            return 0
        size = len(chunk)
        buffer[:size] = chunk
        self.bytes_read += size
        return size


@dataclass(frozen=True)
class ParsedRow:
    series_reference: str
    period: date
    data_value: Decimal | None
    status: str
    units: str
    magnitude: str | None
    subject: str | None
    group: str | None
    series_title_1: str | None
    series_title_2: str | None
    series_title_3: str | None
    series_title_4: str | None
    series_title_5: str | None


@dataclass
class ValidationCounts:
    row_count: int = 0
    missing_data_value_count: int = 0
    invalid_period_count: int = 0
    invalid_data_value_count: int = 0
    invalid_status_count: int = 0
    invalid_units_count: int = 0
    invalid_series_reference_count: int = 0

    def findings(self, fieldnames: list[str] | None) -> dict[str, Any]:
        actual_header = fieldnames or []
        return {
            "header": {
                "valid": tuple(actual_header) == CSV_COLUMNS,
                "expected": list(CSV_COLUMNS),
                "actual": actual_header,
            },
            "invalid_series_reference_count": self.invalid_series_reference_count,
        }


@dataclass
class SeriesAccumulator:
    units: str
    values: list[tuple[date, Decimal]] = field(default_factory=list)

    def add(self, row: ParsedRow) -> None:
        if row.data_value is not None:
            self.values.append((row.period, row.data_value))


def parse_period(value: str | None) -> date | None:
    match = PERIOD_PATTERN.fullmatch((value or "").strip())
    if match is None:
        return None
    return date(int(match["year"]), int(match["month"]), 1)


def parse_decimal(value: str | None) -> Decimal | None:
    normalized = (value or "").strip()
    if not normalized:
        return None
    try:
        result = Decimal(normalized)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _clean_optional(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


def validate_row(row: dict[str | None, str | list[str] | None], counts: ValidationCounts) -> None:
    counts.row_count += 1
    if not _clean_optional(row.get("Data_value") if isinstance(row.get("Data_value"), str) else None):
        counts.missing_data_value_count += 1
    if parse_period(row.get("Period") if isinstance(row.get("Period"), str) else None) is None:
        counts.invalid_period_count += 1
    data_value = row.get("Data_value")
    if isinstance(data_value, str) and data_value.strip() and parse_decimal(data_value) is None:
        counts.invalid_data_value_count += 1
    if not _clean_optional(row.get("STATUS") if isinstance(row.get("STATUS"), str) else None):
        counts.invalid_status_count += 1
    if not _clean_optional(row.get("UNITS") if isinstance(row.get("UNITS"), str) else None):
        counts.invalid_units_count += 1
    if not _clean_optional(
        row.get("Series_reference") if isinstance(row.get("Series_reference"), str) else None
    ):
        counts.invalid_series_reference_count += 1


def parse_valid_row(row: dict[str | None, str | list[str] | None]) -> ParsedRow | None:
    if None in row:
        return None
    required = {
        column: row.get(column) if isinstance(row.get(column), str) else None
        for column in CSV_COLUMNS
    }
    series_reference = _clean_optional(required["Series_reference"])
    period = parse_period(required["Period"])
    status = _clean_optional(required["STATUS"])
    units = _clean_optional(required["UNITS"])
    raw_value = required["Data_value"]
    data_value = parse_decimal(raw_value)
    if (
        series_reference is None
        or period is None
        or status is None
        or units is None
        or (raw_value is not None and raw_value.strip() and data_value is None)
    ):
        return None
    return ParsedRow(
        series_reference=series_reference,
        period=period,
        data_value=data_value,
        status=status,
        units=units,
        magnitude=_clean_optional(required["MAGNITUDE"]),
        subject=_clean_optional(required["Subject"]),
        group=_clean_optional(required["Group"]),
        series_title_1=_clean_optional(required["Series_title_1"]),
        series_title_2=_clean_optional(required["Series_title_2"]),
        series_title_3=_clean_optional(required["Series_title_3"]),
        series_title_4=_clean_optional(required["Series_title_4"]),
        series_title_5=_clean_optional(required["Series_title_5"]),
    )


@contextmanager
def stream_csv_rows(
    s3_client: Any,
    *,
    bucket_name: str,
    object_key: str,
) -> Iterator[tuple[csv.DictReader, Callable[[], int], int]]:
    """Yield a parsed CSV reader, byte-progress function, and object size."""
    response = s3_client.get_object(Bucket=bucket_name, Key=object_key)
    body = response["Body"]
    counting_body = _CountingBody(body)
    text_stream = io.TextIOWrapper(
        io.BufferedReader(counting_body), encoding="utf-8-sig", newline=""
    )
    try:
        reader = csv.DictReader(text_stream)
        yield reader, lambda: counting_body.bytes_read, int(response.get("ContentLength") or 0)
    finally:
        text_stream.close()
        body.close()


def require_expected_header(fieldnames: list[str] | None) -> None:
    if tuple(fieldnames or []) != CSV_COLUMNS:
        raise InvalidCsvHeaderError("CSV header must contain the expected 13 columns in order")


def progress_from_bytes(bytes_read: int, total_bytes: int) -> int:
    if total_bytes <= 0:
        return 0
    return min(99, max(0, int((bytes_read * 100) / total_bytes)))


def _dialect_insert(session: Session, model: Any) -> Any:
    dialect_name = session.bind.dialect.name if session.bind is not None else ""
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        return None
    return insert(model)


def _upsert(
    session: Session,
    model: Any,
    rows: list[dict[str, Any]],
    *,
    index_columns: list[str],
    update_columns: list[str],
) -> None:
    if not rows:
        return
    statement = _dialect_insert(session, model)
    if statement is not None:
        statement = statement.values(rows)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=index_columns,
                set_={column: getattr(statement.excluded, column) for column in update_columns},
            )
        )
        return

    for row in rows:
        criteria = {column: row[column] for column in index_columns}
        existing = session.scalar(select(model).filter_by(**criteria))
        if existing is None:
            session.add(model(**row))
        else:
            for column in update_columns:
                setattr(existing, column, row[column])


def upsert_validation_profile(
    session: Session,
    *,
    ingestion_run_id: Any,
    counts: ValidationCounts,
    fieldnames: list[str] | None,
) -> None:
    values = {
        "ingestion_run_id": ingestion_run_id,
        "row_count": counts.row_count,
        "missing_data_value_count": counts.missing_data_value_count,
        "invalid_period_count": counts.invalid_period_count,
        "invalid_data_value_count": counts.invalid_data_value_count,
        "invalid_status_count": counts.invalid_status_count,
        "invalid_units_count": counts.invalid_units_count,
        "findings": counts.findings(fieldnames),
    }
    _upsert(
        session,
        RunValidationProfile,
        [values],
        index_columns=["ingestion_run_id"],
        update_columns=[column for column in values if column != "ingestion_run_id"],
    )


def upsert_observations(session: Session, rows: list[dict[str, Any]]) -> None:
    _upsert(
        session,
        GdpObservation,
        rows,
        index_columns=["ingestion_run_id", "source_row_number"],
        update_columns=[
            "series_reference",
            "period",
            "data_value",
            "status",
            "units",
            "magnitude",
            "subject",
            "group",
            "series_title_1",
            "series_title_2",
            "series_title_3",
            "series_title_4",
            "series_title_5",
        ],
    )


def upsert_series_summaries(session: Session, rows: list[dict[str, Any]]) -> None:
    _upsert(
        session,
        GdpSeriesSummary,
        rows,
        index_columns=["ingestion_run_id", "series_reference"],
        update_columns=[
            "units",
            "valid_observation_count",
            "first_period",
            "first_value",
            "latest_period",
            "latest_value",
            "min_value",
            "max_value",
            "quarter_to_quarter_change",
        ],
    )
