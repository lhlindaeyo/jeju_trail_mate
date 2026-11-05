"""Application settings loaded from environment variables / .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"  # development | production
    database_url: str = "sqlite:///./jeju_trail_mate.db"
    cors_origins: str = "http://localhost:5173"

    # Auth: dev tokens are signed with this secret. In production, set SUPABASE_JWT_SECRET
    # and the API will verify Supabase-issued access tokens instead.
    jwt_secret: str = "change-me-dev-secret-at-least-32-bytes-long"
    supabase_jwt_secret: str | None = None
    admin_token: str | None = None  # required for /v1/admin/* (course & notice management)

    # AI
    anthropic_api_key: str | None = None
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "medium"
    llm_provider: str = "auto"  # auto | anthropic | mock

    # External data (all optional — features degrade gracefully when missing)
    kma_service_key: str | None = None  # 공공데이터포털 기상청 단기예보 serviceKey
    tour_api_key: str | None = None  # 한국관광공사 TourAPI serviceKey

    # Storage
    storage_dir: str = "./storage"
    signed_url_ttl_seconds: int = 300
    max_upload_mb: int = 15

    # Rules engine thresholds
    delay_threshold_ratio: float = 0.15
    min_gps_accuracy_m: float = 50.0
    notification_window_minutes: int = 30

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
