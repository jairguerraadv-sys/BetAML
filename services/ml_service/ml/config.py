from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = "postgresql+asyncpg://betaml:devpass@postgres:5432/betaml"

    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "betaml"

    REDIS_URL: str = "redis://redis:6379/0"

    CLICKHOUSE_HOST: str = "clickhouse"
    CLICKHOUSE_PORT: int = 9000
    CLICKHOUSE_DB: str = "default"
    CLICKHOUSE_USER: str = "default"
    CLICKHOUSE_PASSWORD: str = ""

    MODEL_ARTIFACTS_PREFIX: str = "ml-models"

    # IsolationForest hyperparameters
    IF_CONTAMINATION: float = 0.1
    IF_N_ESTIMATORS: int = 100
    IF_RANDOM_STATE: int = 42

    API_KEY: str = "dev-secret"


settings = Settings()
