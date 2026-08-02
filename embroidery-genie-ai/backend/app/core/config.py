"""Application settings.

All configuration comes from the environment (12-factor).  Nothing here has a
secret default — the app refuses to start in production if a required secret is
missing rather than silently running insecurely.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # ---------------------------------------------------------------- runtime
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    app_name: str = "Embroidery Genie AI"
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"

    # ------------------------------------------------------------------- http
    # NoDecode: pydantic-settings would otherwise try to JSON-parse the env
    # value before the validator below can split a plain comma-separated list.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    max_upload_mb: int = 25

    # --------------------------------------------------------------- database
    database_url: str = "postgresql+psycopg://genie:genie@localhost:5432/embroidery_genie"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    # ------------------------------------------------------------- supabase
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_key: str = ""
    supabase_jwt_secret: str = ""
    supabase_storage_bucket: str = "designs"

    # Allow unauthenticated local development without Supabase configured.
    allow_dev_auth: bool = False
    dev_user_email: str = "dev@embroiderygenie.ai"

    # ------------------------------------------------------------------- ai
    openai_api_key: str = ""
    openai_vision_model: str = "gpt-4o"
    anthropic_api_key: str = ""
    anthropic_vision_model: str = "claude-sonnet-4-5"
    ai_provider: Literal["openai", "anthropic", "auto", "none"] = "auto"
    ai_timeout_seconds: int = 45

    # -------------------------------------------------------------- storage
    storage_backend: Literal["supabase", "local"] = "local"
    local_storage_path: str = "./storage"
    signed_url_ttl_seconds: int = 3600

    # ---------------------------------------------------------------- limits
    free_designs_per_month: int = 5
    pro_designs_per_month: int = 0        # 0 = unlimited
    business_designs_per_month: int = 0
    max_stitch_preview_points: int = 60000

    # ------------------------------------------------------------- pricing
    default_currency: str = "USD"
    default_labor_rate_per_hour: float = 28.0
    default_machine_rate_per_hour: float = 18.0
    default_thread_cost_per_1000_stitches: float = 0.12
    default_digitizing_fee: float = 45.0
    default_wholesale_margin: float = 0.35
    default_retail_margin: float = 0.55

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value):
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def validate_runtime(self) -> list[str]:
        """Return a list of configuration problems (empty means healthy)."""
        problems: list[str] = []
        if self.is_production:
            if not self.supabase_jwt_secret and not self.supabase_url:
                problems.append(
                    "SUPABASE_JWT_SECRET or SUPABASE_URL must be set in production."
                )
            if self.allow_dev_auth:
                problems.append("ALLOW_DEV_AUTH must be false in production.")
            if "localhost" in self.database_url:
                problems.append("DATABASE_URL still points at localhost.")
        return problems

    def ai_enabled(self) -> bool:
        if self.ai_provider == "none":
            return False
        return bool(self.openai_api_key or self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
