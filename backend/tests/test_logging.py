import json
import logging
import sys

from app.core.logging import JsonFormatter


def format_record(**extra: object) -> dict:
    record = logging.makeLogRecord({"name": "test", "levelname": "INFO", "msg": "hello", **extra})
    return json.loads(JsonFormatter().format(record))


def test_log_line_carries_run_and_task_ids() -> None:
    line = format_record(run_id="run-1", task_id="task-1", task_type="LOAD_OBSERVATIONS")

    assert line["message"] == "hello"
    assert line["run_id"] == "run-1"
    assert line["task_id"] == "task-1"
    assert line["task_type"] == "LOAD_OBSERVATIONS"


def test_log_line_without_context_has_no_id_fields() -> None:
    assert "run_id" not in format_record()


def test_log_line_includes_the_exception() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        line = format_record(exc_info=sys.exc_info())

    assert "ValueError: boom" in line["exception"]
