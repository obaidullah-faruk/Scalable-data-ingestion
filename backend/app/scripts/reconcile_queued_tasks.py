"""Publish durable queued tasks that were not published before a process stop.

Run with ``python -m app.scripts.reconcile_queued_tasks`` from ``backend/``.
"""

import logging

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.services.task_dispatch import reconcile_queued_tasks


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    with SessionLocal() as session:
        count = reconcile_queued_tasks(session)
    logging.getLogger(__name__).info("Reconciled %s queued task(s)", count)


if __name__ == "__main__":
    main()
