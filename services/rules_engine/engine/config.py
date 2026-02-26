"""Pydantic-settings configuration for the rules_engine service."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_consumer_group: str = "rules-engine"

    redis_url: str = "redis://localhost:6379/0"

    # Sync DB URL (psycopg2); asyncpg prefix is normalised automatically.
    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/betaml"

    # Rule cache refresh interval in seconds
    rules_cache_ttl_seconds: int = 60

    # Alerts with risk_score >= this value trigger case creation
    high_severity_threshold: float = 0.7

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
