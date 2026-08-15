"""Best-effort Redis publication of already-committed worker progress."""

import json
import logging
from functools import lru_cache
from typing import Any

import redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

PROGRESS_CHANNEL = "ingestion-run-progress"


@lru_cache
def get_progress_publisher() -> redis.Redis:
    return redis.Redis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1,
    )


def publish_progress_event(event: dict[str, Any]) -> None:
    """Publish without invalidating the durable database checkpoint on failure."""
    try:
        get_progress_publisher().publish(PROGRESS_CHANNEL, json.dumps(event, default=str))
    except redis.RedisError:
        logger.warning("Could not publish committed ingestion progress", exc_info=True)
