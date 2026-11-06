"""Application settings loaded from environment variables / .env."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parent.parent  # paths below do not depend on the working directory


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"  # development | production
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'jeju_trail_mate.db').as_posix()}"
    cors_origins: str = "http://localhost:5173"

    # Auth: dev tokens are signed with this secret. In production, set SUPABASE_JWT_SECRET
    # and the API will verify Supabase-issued access tokens instead.
    jwt_secret: str = "change-me-dev-secret-at-least-32-bytes-long"
    supabase_jwt_secret: str | None = None
    admin_token: str | None = None  # required for /v1/admin/* (course & notice management)

    # AI. LLM_CHAIN (comma-separated, tried in order) wins over LLM_PROVIDER when set.
    # Names: ollama | gemini | groq | openrouter | openai (any OpenAI-compatible URL) | anthropic | mock
    anthropic_api_key: str | None = None
    llm_model: str = "claude-opus-5-5"  # Anthropic model
    llm_effort: str = "medium"
    llm_provider: str = "auto"  # auto | anthropic | mock   (single provider; ignored when LLM_CHAIN is set)
    llm_chain: str = ""
    llm_timeout_s: float = 120.0  # CPU-only local models can be slow
    llm_max_turns: int = 4  # tool-loop turns for OpenAI-compatible providers
    # API keys may be comma-separated: they are rotated on 401/403/429.
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "qwen3:4b"
    gemini_api_key: str | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    gemini_model: str = "gemini-2.5-flash"
    groq_api_key: str | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "llama-3.3-70b-versatile"
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "qwen/qwen3-14b:free"
    openai_api_key: str | None = None  # "openai" = any OpenAI-compatible server (vLLM, llama.cpp, LM Studio, …)
    openai_base_url: str = ""
    openai_model: str = ""

    # External data (all optional — features degrade gracefully when missing)
    kma_service_key: str | None = None  # 공공데이터포털 기상청 단기예보 serviceKey
    jeju_datahub_appkey: str | None = None  # 제주데이터허브 프로젝트키 (공공와이파이 AP OpenAPI)

    # Geocoding (import time). GEOCODER_CHAIN is tried in order; results of providers whose terms
    # forbid storing them (VWorld, Kakao by default) are shown as suggestions, never written to the DB.
    geocoder_chain: str = "local,vworld,kakao"
    juso_entrc_dir: str = str(BACKEND_DIR.parent / "data" / "raw" / "juso_entrc")  # 위치정보 요약DB
    vworld_api_key: str | None = None  # 브이월드 Geocoder/Search 2.0 인증키
    vworld_domain: str | None = None  # 키 발급 때 등록한 도메인
    kakao_rest_api_key: str | None = None  # 선택: 주소/장소명 → 좌표
    kakao_storable: bool = False  # 카카오 약관상 결과 저장이 허용됨을 확인한 경우에만 true

    # Storage
    storage_dir: str = str(BACKEND_DIR / "storage")
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
