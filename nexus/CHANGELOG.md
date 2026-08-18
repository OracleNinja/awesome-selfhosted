# Changelog

All notable changes to NEXUS are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Until 1.0.0 the API and schema may change between minor versions; each such
change is listed here with its migration path.

## [Unreleased]

### Added — M1: platform foundation

- **Configuration** (`nexus/core/config.py`): validated, immutable settings
  loaded from `NEXUS_*` environment variables. Production enforces a real
  signing key, secure session cookies, and an explicit CORS origin list.
  Invalid configuration aborts startup with a message naming the variable and
  exits `78` (`EX_CONFIG`).
- **`monitored_networks` safety boundary**: operator-declared CIDR blocks,
  defaulting to empty. `0.0.0.0/0` is rejected. This is the gate every future
  active network operation must pass.
- **Structured logging** (`nexus/core/logging.py`): JSON Lines output,
  request-id correlation via `contextvars`, and two-layer secret redaction — by
  field name and by pattern within free text (URL credentials, bearer tokens).
- **Error model** (`nexus/core/errors.py`, `nexus/api/errors.py`): one JSON
  error envelope for the whole API. Internal exceptions are replaced with a
  generic `INTERNAL_ERROR` plus a correlation id; validation failures are
  stripped of the submitted values.
- **Authorization model** (`nexus/core/rbac.py`): `ADMIN`, `OPERATOR`, `VIEWER`
  roles resolving to explicit permission sets, with a declared list of
  sensitive permissions requiring confirmation and a reason.
- **Database layer** (`nexus/db/`): async SQLAlchemy 2.0 over asyncpg with
  connection pooling, pre-ping, server-side statement timeouts, and a
  unit-of-work session that commits once or rolls back entirely.
- **Degraded-mode startup**: an unreachable database no longer prevents the
  process from starting; it reports not-ready and recovers without a restart.
- **Migrations**: Alembic configured for the async engine, with the database URL
  taken from validated settings rather than a committed file. Revision
  `0001_foundation` creates `users`, `user_sessions`, `audit_events`, and
  `app_settings`, with a verified `downgrade`.
- **Hash-chained audit schema**: `audit_events` with `prev_hash`/`entry_hash`,
  `GENERATED ALWAYS AS IDENTITY` ids, and actor details snapshotted so history
  survives user deletion.
- **HTTP hardening**: Content-Security-Policy, `nosniff`, `X-Frame-Options`,
  `Referrer-Policy`, `Permissions-Policy`, HSTS in production, request body
  size limits, and proxy-aware client-IP resolution that ignores
  `X-Forwarded-For` unless trusted hops are configured.
- **Health endpoints**: `/api/v1/health/live`, `/health/ready`, `/health`, with
  per-component status and scrubbed diagnostics.
- **OpenAPI documentation** at `/api/docs`, generated from the schemas.
- **Test suite**: 85 tests against a real PostgreSQL instance, covering
  configuration safety rules, redaction, schema constraints, transaction
  rollback, and failure paths (database down, oversized bodies, forged
  request ids, credentials in error text).

### Security

- Fixed a leak found by the suite's own test: component `detail` strings were
  returned verbatim from the unauthenticated `/health` endpoints. Free-text
  scrubbing is now applied at that boundary as well as in the logger.

### Not yet implemented

Authentication endpoints, the audit-writing service, sensors, ingestion,
detection, threat intelligence, workers, real-time streaming, the frontend, the
laboratory, quarantine, and backup tooling. See the README for the current list.
