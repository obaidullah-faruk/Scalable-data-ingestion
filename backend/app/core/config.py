from functools import lru_cache
from typing import Self
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "Scalable Data Ingestion"
    log_level: str = "INFO"
    react_origin: str = "http://localhost:3000"
    database_url: str = ""
    postgres_db: str = "ingestion"
    postgres_user: str = "ingestion"
    postgres_password: str = "ingestion"
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout_seconds: int = 30
    db_pool_recycle_seconds: int = 1800
    celery_broker_url: str = "amqp://ingestion:ingestion@rabbitmq:5672//"
    redis_url: str = "redis://redis:6379/0"
    floci_endpoint_url: str = "http://floci:4566"
    floci_browser_endpoint_url: str = "http://localhost:4566"
    s3_upload_bucket: str = "csv-ingestion-uploads"
    upload_part_size_bytes: int = 8 * 1024 * 1024
    max_upload_size_bytes: int = 5 * 1024 * 1024 * 1024
    part_url_batch_limit: int = 100
    presigned_url_expiration_seconds: int = 15 * 60
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"
    aws_default_region: str = "us-east-1"

    @model_validator(mode="after")
    def build_database_url(self) -> Self:
        if not self.database_url:
            user = quote_plus(self.postgres_user)
            password = quote_plus(self.postgres_password)
            database = quote_plus(self.postgres_db)
            self.database_url = (
                f"postgresql+psycopg://{user}:{password}"
                f"@{self.postgres_host}:{self.postgres_port}/{database}"
            )
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [self.react_origin]


@lru_cache
def get_settings() -> Settings:
    return Settings()
