"""Pydantic-settings configuration for the stream_processor service."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_consumer_group: str = "stream-processor"

    redis_url: str = "redis://localhost:6379/0"
    redis_feature_ttl: int = 86400  # 24 hours in seconds

    clickhouse_host: str = "localhost"
    clickhouse_user: str = "default"
    clickhouse_password: str = ""
    clickhouse_db: str = "default"

    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "betaml"

    # Read tenant configs (not used directly here, available for future use)
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/betaml"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
