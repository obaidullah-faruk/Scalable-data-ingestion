from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title=settings.app_name)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)
    # Request counts and latencies at /metrics. An SSE stream counts as a request, but the
    # time it stays open is not latency.
    Instrumentator(
        excluded_handlers=["/health/.*", "/metrics"],
        should_exclude_streaming_duration=True,
    ).instrument(app).expose(app, include_in_schema=False)
    return app
