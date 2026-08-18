# NEXUS

Self-hosted network security monitoring and cybersecurity laboratory platform.

NEXUS watches a network you own, turns what it observes into normalised
security events, applies detection logic to them, and gives you a security
operations console to investigate and respond. It also provides an isolated
laboratory for practising detection engineering and incident response safely.

> **Status: under construction.** This README describes what is *implemented
> today*, not what is planned. See [Implemented](#implemented) and
> [Not yet implemented](#not-yet-implemented). Nothing in this project
> fabricates network data — see [Honesty rules](#honesty-rules).

---

## Honesty rules

These are non-negotiable constraints on the whole codebase:

- No fabricated traffic, devices, threat intelligence, or detections. Ever.
- A capability that is not configured reports `NOT_CONFIGURED`; a capability
  that is configured but failing reports `UNAVAILABLE`. Neither is allowed to
  render as an empty result that could be mistaken for "nothing found".
- Laboratory data is generated on purpose and is labelled `SIMULATION`
  everywhere it appears, in the database and in the UI.
- Active operations (quarantine) act only through infrastructure you have
  explicitly configured, only against addresses inside networks you have
  declared you own, and never against the target device itself.

## Implemented

Milestones 1–2 — platform foundation, authentication and audit:

| Area | What works |
|---|---|
| Configuration | Validated, immutable settings from the environment; production-specific safety rules; fails at startup with a message naming the variable to fix |
| Logging | Structured JSON logs, request-id correlation, two-layer secret redaction (by key and by pattern in free text) |
| Errors | One error envelope for the whole API; internal exceptions never reach clients |
| Database | PostgreSQL via async SQLAlchemy 2.0, pooling, statement timeouts, degraded-mode start when the database is down |
| Migrations | Alembic, forward and reverse, verified against the models in CI |
| Schema | `users`, `user_sessions`, `audit_events` (hash-chained), `app_settings` |
| Authorization model | `ADMIN` / `OPERATOR` / `VIEWER` roles resolving to explicit permission sets |
| HTTP | Security headers, body-size limits, CORS, correlation ids, OpenAPI docs |
| Health | `/health/live`, `/health/ready`, `/health` with per-component status |
| Authentication | Argon2id passwords, opaque revocable server-side sessions, per-account lockout, login rate limiting, no default credentials |
| Authorization | Permission checks enforced as dependencies; denials audited; role changes revoke sessions immediately |
| CSRF | `SameSite=Strict` cookie, hashed double-submit token, constant-time comparison, enforced for every authenticated write |
| Audit log | Hash-chained, transaction-bound, tamper-evident, with verification via API and CLI |
| User administration | Create, update, role change, deactivate, password reset, unlock — each audited, each guarded |
| CLI | `create-admin`, `verify-audit`, `show-config` |
| CI | Format, lint, types, tests on PostgreSQL 16 and 17, migration round-trip, dependency audit, and startup-safety checks |
| Tests | 206 tests against a real PostgreSQL instance, including failure and attack paths |

## Not yet implemented

Listed so nothing here reads as a promise that the code already keeps:

- Network sensors, event ingestion, normalisation
- Detection engine, risk scoring, threat intelligence
- Background workers and the job queue
- Real-time streaming to the UI
- The frontend
- The cybersecurity laboratory
- Quarantine
- Backup and restore tooling

## Quick start (development)

Requirements: Python 3.11+, PostgreSQL 16+.

```bash
cd nexus/backend

python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# Create the database (adjust for your local PostgreSQL setup)
createuser nexus --pwprompt      # or: CREATE ROLE nexus LOGIN PASSWORD 'nexus';
createdb -O nexus nexus
createdb -O nexus nexus_test

cp .env.example .env             # then edit NEXUS_DATABASE_URL if needed
.venv/bin/alembic upgrade head

# Create the first administrator. There is no default account.
.venv/bin/python -m nexus.cli create-admin --username ian

.venv/bin/python -m nexus.main
```

Then:

- API docs: <http://127.0.0.1:8000/api/docs>
- Health: <http://127.0.0.1:8000/api/v1/health>

## Running the checks

```bash
cd nexus/backend
.venv/bin/ruff format --check .     # formatting
.venv/bin/ruff check .              # linting, including security rules
.venv/bin/mypy nexus                # type checking
.venv/bin/python -m pytest          # tests (needs PostgreSQL)
```

Tests run against a real PostgreSQL database (`nexus_test` by default,
overridable with `NEXUS_TEST_DATABASE_URL`). They truncate every table, so the
URL must point at a database whose name contains `test` — the suite refuses to
start otherwise.

## Documentation

| Document | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Subsystem boundaries, data flow, and the reasoning behind each major decision |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model, every control, and what each control does *not* protect against |
| [docs/API.md](docs/API.md) | API conventions: versioning, auth, error codes, pagination |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Local setup, workflow, testing strategy, adding a migration |
| [CHANGELOG.md](CHANGELOG.md) | Version history |

`DEPLOYMENT.md`, `OPERATIONS.md` and `TROUBLESHOOTING.md` are written as the subsystems they describe are built, so that no
document describes functionality that does not exist.

## Licence

MIT.
