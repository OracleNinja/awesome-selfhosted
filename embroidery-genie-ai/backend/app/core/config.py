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
    anthropic_api_key: str = ""
    ai_provider: Literal["openai", "anthropic", "auto", "none"] = "auto"
    ai_timeout_seconds: int = 45

    # --- kill switches --------------------------------------------------
    # Global. With this off the product still analyses, prices, digitizes,
    # validates and exports — only the optional semantic description goes away.
    ai_enabled: bool = True
    # Per-operation. Lets one expensive operation be shut off without taking
    # the whole AI layer down with it.
    vision_analysis_enabled: bool = True
    # Conservative limits, aggressive caching, detailed tracking. On by default
    # so a fresh deployment is cheap before anyone has thought about budgets.
    ai_beta_cost_mode: bool = True

    # --- model routing --------------------------------------------------
    # Business logic asks for a *tier*, never a model name. These four strings
    # are the only place a model id appears. See app/ai/routing.py.
    ai_model_anthropic_low: str = "claude-haiku-4-5"
    ai_model_anthropic_medium: str = "claude-sonnet-5"
    ai_model_anthropic_high: str = "claude-opus-5"
    ai_model_anthropic_vision: str = "claude-haiku-4-5"
    ai_model_openai_low: str = "gpt-4o-mini"
    ai_model_openai_medium: str = "gpt-4o-mini"
    ai_model_openai_high: str = "gpt-4o"
    ai_model_openai_vision: str = "gpt-4o-mini"

    # --- per-request bounds ---------------------------------------------
    # Artwork is downscaled to this long edge before it is sent. Vision input
    # cost scales with pixel area, and nothing in the semantic pass needs
    # print resolution.
    ai_image_max_edge_px: int = 1024
    ai_image_jpeg_quality: int = 82
    # Hard ceilings. A request that cannot be brought under these is rejected
    # rather than sent.
    ai_max_input_tokens: int = 4000
    ai_max_output_tokens: int = 700
    ai_max_attempts: int = 3
    ai_retry_backoff_seconds: float = 1.0

    # --- budgets ---------------------------------------------------------
    # Two units at every scope. Tokens always bind; dollars bind only when
    # pricing has been configured (see config/ai_pricing.json). 0 = no limit.
    ai_daily_tokens_per_user: int = 120_000
    ai_daily_tokens_per_tenant: int = 400_000
    ai_monthly_tokens_per_tenant: int = 4_000_000
    ai_monthly_tokens_global: int = 60_000_000
    ai_daily_cost_per_user_usd: float = 0.0
    ai_daily_cost_per_tenant_usd: float = 0.0
    ai_monthly_cost_per_tenant_usd: float = 25.0
    ai_monthly_cost_global_usd: float = 400.0
    # An administrator can deliberately let spend continue past 100%.
    ai_budget_override: bool = False

    # --- caching ----------------------------------------------------------
    ai_cache_enabled: bool = True
    ai_cache_ttl_days: int = 30

    # --- cost accounting --------------------------------------------------
    # Rates live in a JSON file, not in code. Absent or unpriced models record
    # tokens with a null cost rather than a guessed one.
    ai_pricing_file: str = "config/ai_pricing.json"
    # Comma-separated emails allowed to see cross-tenant (global) cost figures.
    ai_admin_emails: Annotated[list[str], NoDecode] = Field(default_factory=list)

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

    @field_validator("cors_origins", "ai_admin_emails", mode="before")
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

        # The app sends credentials, so a wildcard origin would let any site
        # read authenticated responses. Browsers reject this pairing anyway;
        # failing loudly here beats a confusing CORS error at runtime.
        if "*" in self.cors_origins:
            problems.append(
                "CORS_ORIGINS cannot be '*' because the API allows credentials. "
                "List the exact origins that serve the app."
            )

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

    def ai_available(self) -> bool:
        """True when an AI call could actually be made right now.

        Three independent things have to line up: the global kill switch, a
        provider selection other than ``none``, and a credential. Any of them
        being off is a normal, supported state — the product runs without AI.
        """
        if not self.ai_enabled:
            return False
        if self.ai_provider == "none":
            return False
        return bool(self.openai_api_key or self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
