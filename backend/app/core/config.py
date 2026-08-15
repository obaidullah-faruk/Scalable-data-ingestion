from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "Scalable Data Ingestion"
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:5173"]
    database_url: str = "postgresql+psycopg://ingestion:ingestion@postgres:5432/ingestion"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout_seconds: int = 30
    db_pool_recycle_seconds: int = 1800
    celery_broker_url: str = "amqp://ingestion:ingestion@rabbitmq:5672//"
    redis_url: str = "redis://redis:6379/0"
    floci_endpoint_url: str = "http://floci:4566"
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"
    aws_default_region: str = "us-east-1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
