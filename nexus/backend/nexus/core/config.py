"""Configuration loading and validation.

Why this module exists
----------------------
Every other subsystem needs to know things it cannot discover for itself:
where the database lives, which networks the operator owns, how long to keep
security events. Scattering ``os.environ`` reads through the codebase makes
those requirements invisible — you discover a missing variable when a request
fails at 3am, not when the process starts.

This module concentrates configuration into one validated, immutable object
built exactly once at startup. If configuration is wrong, the process refuses
to start and prints what is wrong. That is deliberate: a security tool that
runs with a broken configuration is worse than one that does not run, because
the operator believes they are being monitored when they are not.

Design notes
------------
* Source of truth is the process environment (12-factor). A ``.env`` file is
  read for developer convenience only.
* Secrets are wrapped in ``SecretStr`` so an accidental ``repr()`` of the
  settings object — in a log line, an error page, a debugger — cannot leak
  them. Retrieving the real value requires an explicit
  ``.get_secret_value()`` call, which is easy to grep for in review.
* Rules that only matter in production (real secret key, no permissive CORS)
  are enforced in production only, so development stays frictionless without
  weakening the deployed system.
"""

from __future__ import annotations

import ipaddress
import json
import os
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from pydantic_settings.exceptions import SettingsError

# Environment variables are read with this prefix, e.g. NEXUS_DATABASE_URL.
ENV_PREFIX = "NEXUS_"

# A development-only session secret. Production refuses to start with it, which
# is the point: a value that works everywhere is a value that ends up deployed.
# The linter flags this as a hardcoded credential; it is a sentinel, and its
# whole purpose is to be recognisable so that production can refuse to start.
DEV_SECRET_KEY = "dev-insecure-secret-key-do-not-use-in-production"  # noqa: S105

MIN_SECRET_KEY_LENGTH = 32


class Environment(str, Enum):
    """Deployment environment.

    The value changes safety defaults, not features. ``testing`` exists so the
    test suite can opt out of behaviour that is hostile to tests (rate limits,
    real sleep-based backoff) without those switches being reachable in
    production.
    """

    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"

    @property
    def is_production(self) -> bool:
        return self is Environment.PRODUCTION


class LogFormat(str, Enum):
    JSON = "json"
    CONSOLE = "console"


class ConfigurationError(RuntimeError):
    """Raised when configuration is missing or invalid.

    Carries a human-readable, multi-line explanation. ``main`` prints it to
    stderr and exits non-zero rather than raising a stack trace at the
    operator, because a traceback about pydantic internals does not tell a
    sysadmin which variable to set.
    """


class Settings(BaseSettings):
    """Validated application configuration.

    Instances are immutable (``frozen=True``). Anything that needs to change at
    runtime — detection thresholds, retention windows an operator tunes in the
    UI — belongs in the database, not here. The rule of thumb: this object holds
    what the process needs in order to reach the database; everything else is
    data.
    """

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=os.environ.get(f"{ENV_PREFIX}ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # ---------------------------------------------------------------- core --
    environment: Environment = Environment.DEVELOPMENT
    app_name: str = "NEXUS"

    # ------------------------------------------------------------ database --
    database_url: SecretStr = SecretStr("postgresql+asyncpg://nexus:nexus@localhost:5432/nexus")
    db_pool_size: Annotated[int, Field(ge=1, le=100)] = 10
    db_max_overflow: Annotated[int, Field(ge=0, le=100)] = 10
    db_pool_timeout_seconds: Annotated[float, Field(gt=0, le=120)] = 10.0
    db_statement_timeout_seconds: Annotated[float, Field(gt=0, le=300)] = 30.0
    db_connect_retries: Annotated[int, Field(ge=0, le=20)] = 5

    # ------------------------------------------------------------- runtime --
    host: str = "127.0.0.1"
    port: Annotated[int, Field(ge=1, le=65535)] = 8000
    data_dir: Path = Path("./var")

    # ------------------------------------------------------------- logging --
    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.JSON

    # -------------------------------------------------------------- crypto --
    secret_key: SecretStr = SecretStr(DEV_SECRET_KEY)

    # Argon2id cost parameters. Defaults follow the OWASP recommendation
    # (m=64 MiB, t=3, p=2) and are configurable for two reasons: costs must rise
    # as hardware gets faster, and the test suite would otherwise spend minutes
    # hashing. Raising them does not invalidate existing hashes — the parameters
    # are encoded in each stored hash, and accounts are upgraded at next login.
    password_hash_time_cost: Annotated[int, Field(ge=1, le=20)] = 3
    password_hash_memory_kib: Annotated[int, Field(ge=8, le=1024 * 1024)] = 64 * 1024
    password_hash_parallelism: Annotated[int, Field(ge=1, le=16)] = 2

    # ------------------------------------------------------------ sessions --
    session_cookie_name: str = "nexus_session"
    session_absolute_ttl_minutes: Annotated[int, Field(ge=5, le=60 * 24 * 30)] = 720
    session_idle_timeout_minutes: Annotated[int, Field(ge=1, le=60 * 24 * 7)] = 60
    # Set false only when NEXUS is reached over plain HTTP on a trusted link
    # (a lab bench). Defaults to true, and production may not turn it off.
    session_cookie_secure: bool = True

    # --------------------------------------------------------------- http ---
    # NoDecode: pydantic-settings would otherwise try to JSON-decode any
    # list-typed field read from the environment *before* validators run, so
    # `NEXUS_CORS_ORIGINS=http://a,http://b` would fail with a JSON parse error
    # instead of reaching the splitter below. NoDecode hands the raw string to
    # the validator, which accepts both the comma-separated and JSON forms.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Requests larger than this are rejected before the body is buffered, so a
    # single client cannot exhaust memory by streaming an enormous JSON body.
    max_request_body_bytes: Annotated[int, Field(ge=1024, le=64 * 1024 * 1024)] = 1 * 1024 * 1024
    # Trusted reverse-proxy hops for client-IP resolution. 0 means "trust no
    # X-Forwarded-For header" — the correct default for a directly exposed app,
    # since otherwise any client can forge its own source address in audit logs.
    trusted_proxy_hops: Annotated[int, Field(ge=0, le=5)] = 0

    # ---------------------------------------------------------- monitoring --
    # CIDR blocks the operator asserts they own. Quarantine and active probing
    # refuse to touch anything outside these ranges; see SECURITY.md.
    monitored_networks: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # ---------------------------------------------------------- retention ---
    event_retention_days: Annotated[int, Field(ge=1, le=3650)] = 90
    audit_retention_days: Annotated[int, Field(ge=30, le=3650)] = 365

    # ---------------------------------------------------------------- audit --
    # Optional off-database mirror of the audit log, as line-delimited JSON.
    # Its value is that it can live on a different filesystem, be owned by a
    # different user, or sit on append-only storage — so tampering with history
    # requires compromising two places rather than one. Unset means the audit
    # log has no external anchor, and the system reports that rather than
    # implying protection it does not have.
    audit_mirror_path: Path | None = None

    # ------------------------------------------------------------ workers ---
    worker_concurrency: Annotated[int, Field(ge=1, le=64)] = 4
    worker_poll_interval_seconds: Annotated[float, Field(gt=0, le=60)] = 1.0
    job_default_timeout_seconds: Annotated[int, Field(ge=1, le=3600)] = 120
    job_max_attempts: Annotated[int, Field(ge=1, le=20)] = 5

    # -------------------------------------------------------- rate limits ---
    login_rate_limit_per_minute: Annotated[int, Field(ge=1, le=1000)] = 10
    api_rate_limit_per_minute: Annotated[int, Field(ge=1, le=100_000)] = 600

    # Per-account lockout. Unlike the rate limit above (which is per process and
    # best-effort), this counter lives on the user row, so it survives restarts
    # and is shared by every worker — an attacker cannot reset their budget by
    # waiting for a deploy.
    login_max_failed_attempts: Annotated[int, Field(ge=3, le=100)] = 8
    login_lockout_minutes: Annotated[int, Field(ge=1, le=1440)] = 15

    # ------------------------------------------------------------ validators --

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        level = value.upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if level not in allowed:
            raise ValueError(f"must be one of {sorted(allowed)}")
        return level

    @field_validator("cors_origins", "monitored_networks", mode="before")
    @classmethod
    def _split_csv(cls, value: Any) -> Any:
        """Accept ``a,b`` as well as a JSON list.

        Operators write ``NEXUS_MONITORED_NETWORKS=192.168.1.0/24,10.0.0.0/8``
        in a systemd unit or compose file; requiring JSON there is a papercut
        that leads to mistakes in exactly the setting that must not be wrong.
        """
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                # The JSON form still has to work: it is what a generated
                # manifest or a config-management tool is most likely to emit.
                # With NoDecode set on these fields, nothing else parses it.
                try:
                    return json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"looks like JSON but is not valid: {exc}") from exc
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @field_validator("monitored_networks")
    @classmethod
    def _validate_networks(cls, value: list[str]) -> list[str]:
        normalised: list[str] = []
        for raw in value:
            try:
                network = ipaddress.ip_network(raw, strict=False)
            except ValueError as exc:
                raise ValueError(f"{raw!r} is not a valid CIDR block: {exc}") from exc
            if network.prefixlen == 0:
                raise ValueError(
                    f"{raw!r} covers the entire address space; NEXUS refuses to "
                    "treat every host on the internet as locally owned"
                )
            normalised.append(str(network))
        return normalised

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: SecretStr) -> SecretStr:
        url = value.get_secret_value()
        if not url.startswith("postgresql+asyncpg://"):
            raise ValueError(
                "must be a PostgreSQL URL using the asyncpg driver, "
                "e.g. postgresql+asyncpg://user:pass@host:5432/nexus"
            )
        return value

    @model_validator(mode="after")
    def _validate_production_rules(self) -> Settings:
        """Rules that are only enforced when it matters.

        Development deliberately runs with a known secret and an insecure
        cookie so a fresh clone works with no setup. Production must not, and
        the check lives here rather than in a deployment checklist because
        checklists are not executed by the process.
        """
        if not self.environment.is_production:
            return self

        problems: list[str] = []
        secret = self.secret_key.get_secret_value()
        if secret == DEV_SECRET_KEY:
            problems.append(
                f"{ENV_PREFIX}SECRET_KEY is still the development default. "
                'Generate one with: python -c "import secrets; '
                'print(secrets.token_urlsafe(48))"'
            )
        elif len(secret) < MIN_SECRET_KEY_LENGTH:
            problems.append(
                f"{ENV_PREFIX}SECRET_KEY must be at least "
                f"{MIN_SECRET_KEY_LENGTH} characters in production"
            )

        if not self.session_cookie_secure:
            problems.append(
                f"{ENV_PREFIX}SESSION_COOKIE_SECURE cannot be disabled in "
                "production; serve NEXUS over HTTPS"
            )

        if "*" in self.cors_origins:
            problems.append(
                f"{ENV_PREFIX}CORS_ORIGINS cannot be '*' in production; "
                "list the exact origins that may call the API"
            )

        if problems:
            raise ValueError("; ".join(problems))
        return self

    # -------------------------------------------------------------- helpers --

    @property
    def monitored_ip_networks(self) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
        """Parsed form of :attr:`monitored_networks`.

        Parsed on access rather than stored, because the settings object is
        frozen and pydantic would otherwise need a custom serialiser for a
        type nothing else in the system passes over the wire.
        """
        return tuple(ipaddress.ip_network(cidr) for cidr in self.monitored_networks)

    def is_monitored_address(self, address: str) -> bool:
        """True when ``address`` falls inside an operator-declared network.

        This is the gate every active operation (quarantine, probe) must pass.
        It fails closed twice over: an unparseable address is not monitored,
        and an empty ``monitored_networks`` list means *nothing* is monitored,
        so an operator who has not declared their network cannot act on it.
        """
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(ip in network for network in self.monitored_ip_networks)

    @property
    def redacted_summary(self) -> dict[str, Any]:
        """Safe-to-log view of the configuration.

        Emitted once at startup so a support conversation can begin with facts
        instead of "what does your config look like?". Secrets are replaced by
        a presence flag, never by a prefix or a length — both leak information.
        """
        return {
            "environment": self.environment.value,
            "host": self.host,
            "port": self.port,
            "log_level": self.log_level,
            "log_format": self.log_format.value,
            "database": _describe_database_url(self.database_url.get_secret_value()),
            "data_dir": str(self.data_dir),
            "monitored_networks": list(self.monitored_networks),
            "cors_origins": list(self.cors_origins),
            "event_retention_days": self.event_retention_days,
            "audit_retention_days": self.audit_retention_days,
            "worker_concurrency": self.worker_concurrency,
            # Named to avoid the log redactor's "secret" key match: this is a
            # boolean fact about configuration, not the value itself, and it is
            # the single most useful line in a startup log.
            "signing_key_configured": self.secret_key.get_secret_value() != DEV_SECRET_KEY,
        }


def _describe_database_url(url: str) -> str:
    """Render a database URL without its password.

    Hand-rolled rather than using ``urlsplit`` so that a malformed URL degrades
    to a constant instead of raising while we are trying to report an error.
    """
    try:
        scheme, _, rest = url.partition("://")
        credentials, _, location = rest.rpartition("@")
        if credentials:
            user = credentials.split(":", 1)[0]
            return f"{scheme}://{user}:***@{location}"
        return f"{scheme}://{location}"
    except Exception:  # pragma: no cover - defensive
        return "<unparseable database url>"


def _format_validation_error(error: ValidationError) -> str:
    lines = ["NEXUS cannot start: configuration is invalid.", ""]
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "<root>"
        variable = (
            f"{ENV_PREFIX}{location.upper()}" if location != "<root>" else "(cross-field rule)"
        )
        lines.append(f"  * {variable}: {item['msg']}")
    lines.append("")
    lines.append("See nexus/backend/.env.example for the full list of settings.")
    return "\n".join(lines)


def load_settings(**overrides: Any) -> Settings:
    """Build a :class:`Settings`, converting validation failures into guidance.

    Keyword overrides exist for tests, which construct settings directly rather
    than mutating ``os.environ`` — mutating the environment makes tests
    order-dependent and is a common source of "passes alone, fails in CI".
    """
    try:
        return Settings(**overrides)
    except ValidationError as exc:
        raise ConfigurationError(_format_validation_error(exc)) from exc
    except SettingsError as exc:
        # Raised by the environment source itself — a malformed value it could
        # not even hand to a validator. Still the operator's problem to fix, so
        # it gets the same treatment rather than a traceback through
        # pydantic-settings internals.
        raise ConfigurationError(
            "NEXUS cannot start: a configuration value could not be parsed.\n\n"
            f"  {exc}\n\n"
            "See nexus/backend/.env.example for the expected format."
        ) from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton.

    Cached because configuration is immutable for the lifetime of the process:
    re-reading the environment mid-run would mean two subsystems could disagree
    about, say, which networks are monitored. Tests clear the cache via
    :func:`reset_settings_cache`.
    """
    return load_settings()


def reset_settings_cache() -> None:
    """Drop the cached settings (tests only)."""
    get_settings.cache_clear()
