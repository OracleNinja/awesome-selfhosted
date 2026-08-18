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

### Added — M2: authentication, authorization enforcement, audit logging

- **Argon2id password hashing** (`nexus/core/passwords.py`) with configurable
  cost parameters, transparent rehashing at next login when parameters rise,
  and hashing performed in a worker thread so it cannot stall the event loop.
- **Password policy**: length-first (12 character minimum), a small breach
  blocklist, an upper bound so an oversized password cannot tie up a worker,
  and CSPRNG-generated passwords for bootstrap and admin resets.
- **Opaque server-side sessions** (`nexus/services/auth.py`): 256-bit tokens
  stored only as SHA-256 digests, absolute and idle expiry, fresh session per
  login (defeating session fixation), and immediate revocation on logout,
  password change, password reset, role change, and deactivation.
- **Login defences**: uniform failure responses, dummy hashing on the
  not-found path so timing cannot enumerate accounts, durable per-account
  lockout, and a per-process login rate limit documented as best-effort.
- **CSRF protection** (`nexus/api/security.py`): `SameSite=Strict` cookie plus
  a hashed double-submit token compared in constant time, enforced inside the
  identity dependency so no authenticated write endpoint can omit it.
- **Authorization enforcement**: `require(Permission.X)` dependencies; denials
  are audited and the audit survives the rejection.
- **Audit service** (`nexus/services/audit.py`): hash-chained append-only
  entries written in the same transaction as the action they describe,
  serialised by a PostgreSQL transaction-scoped advisory lock, with detail
  sanitisation, size limits, chain verification, and an optional
  off-database mirror (`NEXUS_AUDIT_MIRROR_PATH`, off by default).
- **User administration** (`/api/v1/users`): create, update, role change,
  activate/deactivate, password reset, unlock. Sensitive changes require a
  written reason. Guards prevent self-demotion, self-deactivation, and removal
  of the last active administrator.
- **Audit API** (`/api/v1/audit`): keyset-paginated queries with filters, and
  `POST /audit/verify` which reports whether an external anchor is configured
  rather than implying protection the deployment does not have.
- **Operator CLI** (`nexus/cli.py`): `create-admin`, `verify-audit`,
  `show-config`. There is no default account and no bootstrap endpoint; the
  first administrator is created by someone with shell access, and passwords
  are never accepted as command-line arguments.
- **docs/SECURITY.md**: threat model, every control, and an explicit statement
  of what each control does *not* protect against.

### Changed

- Three audit/log field names were chosen to survive the redaction filters
  (`signing_key_configured`, `credential_generated`,
  `force_credential_change`). The filters stay deliberately blunt; the field
  names move out of their way so the log keeps the fact it exists to record.
- `User.role` is typed as `str` in the ORM and converted to `Role` at the
  boundary, matching the `VARCHAR` + `CHECK` storage decision.

### Not yet implemented

Sensors, ingestion, detection, threat intelligence, workers, real-time
streaming, the frontend, the laboratory, quarantine, and backup tooling. See
the README for the current list.
