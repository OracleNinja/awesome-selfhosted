"""Configuration validation tests.

These are the highest-value tests in the foundation: every one of them
corresponds to a way a deployment could be quietly insecure. A test that
production refuses a development secret key is worth more than a hundred tests
of a getter.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nexus.core.config import (
    DEV_SECRET_KEY,
    ConfigurationError,
    Environment,
    Settings,
    load_settings,
)


class TestDefaults:
    def test_development_starts_with_no_configuration(self) -> None:
        config = load_settings(environment=Environment.DEVELOPMENT)
        assert config.environment is Environment.DEVELOPMENT
        assert config.log_level == "INFO"

    def test_monitored_networks_default_to_empty(self) -> None:
        """An operator who has declared nothing owns nothing, as far as NEXUS
        is concerned. Every active operation is then refused."""
        config = load_settings(environment=Environment.DEVELOPMENT)
        assert config.monitored_networks == []
        assert config.is_monitored_address("192.168.1.10") is False


class TestProductionRules:
    def test_production_rejects_development_secret(self) -> None:
        with pytest.raises(ConfigurationError) as exc:
            load_settings(environment=Environment.PRODUCTION, secret_key=DEV_SECRET_KEY)
        assert "SECRET_KEY" in str(exc.value)

    def test_production_rejects_short_secret(self) -> None:
        with pytest.raises(ConfigurationError) as exc:
            load_settings(environment=Environment.PRODUCTION, secret_key="too-short")
        assert "at least" in str(exc.value)

    def test_production_rejects_insecure_cookies(self) -> None:
        with pytest.raises(ConfigurationError) as exc:
            load_settings(
                environment=Environment.PRODUCTION,
                secret_key="x" * 48,
                session_cookie_secure=False,
            )
        assert "SESSION_COOKIE_SECURE" in str(exc.value)

    def test_production_rejects_wildcard_cors(self) -> None:
        with pytest.raises(ConfigurationError) as exc:
            load_settings(
                environment=Environment.PRODUCTION,
                secret_key="x" * 48,
                cors_origins=["*"],
            )
        assert "CORS_ORIGINS" in str(exc.value)

    def test_valid_production_configuration_is_accepted(self) -> None:
        config = load_settings(
            environment=Environment.PRODUCTION,
            secret_key="x" * 48,
            cors_origins=["https://nexus.example.org"],
        )
        assert config.environment.is_production

    def test_development_keeps_the_defaults_that_production_refuses(self) -> None:
        """The relaxed rules must be reachable *only* outside production."""
        config = load_settings(environment=Environment.DEVELOPMENT, session_cookie_secure=False)
        assert config.session_cookie_secure is False


class TestMonitoredNetworks:
    def test_accepts_comma_separated_string(self) -> None:
        config = load_settings(monitored_networks="192.168.1.0/24, 10.0.0.0/8")
        assert config.monitored_networks == ["192.168.1.0/24", "10.0.0.0/8"]

    def test_rejects_invalid_cidr(self) -> None:
        with pytest.raises(ConfigurationError) as exc:
            load_settings(monitored_networks=["not-a-network"])
        assert "MONITORED_NETWORKS" in str(exc.value)

    def test_rejects_the_whole_internet(self) -> None:
        """0.0.0.0/0 would make every address on earth 'locally owned', which
        would disable the single safety check protecting active operations."""
        with pytest.raises(ConfigurationError):
            load_settings(monitored_networks=["0.0.0.0/0"])

    def test_host_addresses_are_normalised_to_their_network(self) -> None:
        config = load_settings(monitored_networks=["192.168.1.73/24"])
        assert config.monitored_networks == ["192.168.1.0/24"]

    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            ("192.168.1.73", True),
            ("192.168.1.255", True),
            ("192.168.2.1", False),
            ("8.8.8.8", False),
            ("not-an-ip", False),
            ("", False),
        ],
    )
    def test_membership(self, address: str, expected: bool) -> None:
        config = load_settings(monitored_networks=["192.168.1.0/24"])
        assert config.is_monitored_address(address) is expected

    def test_ipv6_is_supported(self) -> None:
        config = load_settings(monitored_networks=["fd00::/8"])
        assert config.is_monitored_address("fd00::1") is True
        assert config.is_monitored_address("2001:4860:4860::8888") is False


class TestDatabaseUrl:
    def test_rejects_non_async_driver(self) -> None:
        """A sync driver would block the event loop on every query — the whole
        server stalls behind one slow statement."""
        with pytest.raises(ConfigurationError):
            load_settings(database_url="postgresql://nexus:nexus@localhost/nexus")

    def test_rejects_other_databases(self) -> None:
        with pytest.raises(ConfigurationError):
            load_settings(database_url="sqlite+aiosqlite:///./nexus.db")


class TestSecrets:
    def test_secret_is_not_in_repr(self) -> None:
        config = load_settings(secret_key="super-secret-value-1234567890")
        assert "super-secret-value" not in repr(config)
        assert "super-secret-value" not in str(config)

    def test_database_password_is_not_in_repr(self) -> None:
        config = load_settings(
            database_url="postgresql+asyncpg://nexus:hunter2@localhost:5432/nexus"
        )
        assert "hunter2" not in repr(config)

    def test_redacted_summary_hides_password_but_keeps_shape(self) -> None:
        config = load_settings(
            database_url="postgresql+asyncpg://nexus:hunter2@db.internal:5432/nexus"
        )
        summary = config.redacted_summary
        assert "hunter2" not in str(summary)
        # Still useful for support: the user, host, port and database survive.
        assert "db.internal:5432/nexus" in summary["database"]
        assert summary["signing_key_configured"] is False

    def test_redacted_summary_reports_a_configured_secret(self) -> None:
        config = load_settings(secret_key="x" * 48)
        assert config.redacted_summary["signing_key_configured"] is True


class TestImmutability:
    def test_settings_cannot_be_mutated(self) -> None:
        """Two subsystems must never disagree about configuration mid-run."""
        config = load_settings()
        with pytest.raises(ValidationError):
            config.port = 9999  # type: ignore[misc]


class TestErrorReporting:
    def test_message_names_the_environment_variable(self) -> None:
        """The operator should be told which variable to fix, with its prefix."""
        with pytest.raises(ConfigurationError) as exc:
            load_settings(log_level="LOUD")
        message = str(exc.value)
        assert "NEXUS_LOG_LEVEL" in message
        assert "cannot start" in message.lower()


def test_settings_type_is_exported() -> None:
    assert isinstance(load_settings(), Settings)
