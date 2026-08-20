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

### Added — M3: continuous integration and API documentation

- **CI** (`.github/workflows/nexus-ci.yml`), path-scoped to `nexus/**`:
  formatting, linting (with bandit security rules), type checking, the full
  test suite against PostgreSQL 16 and 17, a migration round-trip with
  `alembic check`, a `pip-audit` dependency audit, and startup-safety checks
  proving production refuses the development signing key while still accepting
  a correctly configured production environment.
- **docs/API.md**: versioning policy, the authentication and CSRF flow, the
  full error-code table, correlation ids, keyset pagination and why it is not
  offset, rate limits, and the three health endpoints.

### Fixed

- **List settings could not be supplied through the environment.**
  `NEXUS_CORS_ORIGINS=a,b` and `NEXUS_MONITORED_NETWORKS=a,b` — the format
  `.env.example` documents — raised a JSON parse error, because
  pydantic-settings decodes list-typed fields *before* field validators run.
  The fields are now annotated `NoDecode`, the validator accepts both the
  comma-separated and JSON forms, and any remaining parse failure is reported
  as a `ConfigurationError` naming the variable rather than as a traceback.

  The bug survived 206 tests because every one of them passed configuration as
  keyword arguments, never through the environment. Tests now cover the path
  operators actually use, including the production safety rules.

### Security

- Raised the lower bounds on `pytest` (>=9.0.3) and `setuptools` (>=83.0.0),
  which carried published advisories, and added `pip-audit` to the dev
  dependencies and to CI.

### Added — M4: sensors, ingestion, and background processing

- **Sensor framework** (`nexus/sensors/`): a driver interface whose
  availability check runs before start and reports `NOT_AVAILABLE` or
  `NOT_CONFIGURED` with a human remedy, and whose `produces_simulated_data`
  class attribute is what stamps `is_simulation` at ingestion — so a
  laboratory driver cannot produce data that looks observed.
- **`arp_discovery`**: reads `/proc/net/arp`, the kernel's own neighbour cache.
  No privileges, no packets sent. Emits on change (discovered, address changed)
  plus an hourly presence heartbeat, so a stable network does not produce tens
  of thousands of identical rows a day.
- **`syslog`**: UDP receiver parsing RFC 3164 and RFC 5424. Records the source
  address from the packet rather than the message, strips control characters,
  and drops with a counter under flood rather than growing without bound.
- **`packet_capture`**: passive `AF_PACKET` capture with a hand-written
  Ethernet/VLAN/IPv4/IPv6/TCP/UDP/ICMP parser, folding frames into flows.
  Requires `CAP_NET_RAW`, never transmits, never stores payload bytes, and
  bounds its flow table so a host spraying random ports cannot exhaust memory.
- **`lab_synthetic`**: the only source of fabricated data in the system.
  Scenarios use RFC 5737 documentation addresses that can never collide with a
  real device, are seeded for reproducibility, and are finite.
- **Sensor manager**: per-sensor supervision with exponential backoff, parking
  after repeated failures, live reload without restarting NEXUS, and a
  30-second reconcile loop for convergence.
- **Ingestion** (`nexus/services/ingest.py`): UUIDv5 deduplication derived from
  each observation's content, MAC-first device identity with a partial unique
  index covering MAC-less devices, batch inserts with `ON CONFLICT DO NOTHING`,
  and counted rejections rather than silent coercion.
- **Job queue** (`nexus/services/jobs.py`): PostgreSQL `FOR UPDATE SKIP LOCKED`
  claim, priorities, per-job timeouts, jittered exponential backoff, DEAD jobs
  for exhausted retries, cooperative cancellation, orphan reclamation using the
  database's clock, and partial-unique dedupe keys.
- **Workers** (`nexus/workers/`): handler registry, in-process or standalone
  (`python -m nexus.worker`), graceful shutdown, heartbeat-extended locks, and
  a leaderless recurring scheduler built on the queue's dedupe index.
- **Maintenance handlers**: batched event retention, audit retention that
  refuses to empty the chain, job purging that keeps failures, and device idle
  reconciliation that never touches a quarantined device.
- **Schema** (migration `0002`): `devices`, `security_events`, `sensors`,
  `jobs` — with a BRIN index on the append-only timestamp, partial indexes for
  the queue claim and for real-network event queries, and a GIN index on event
  attributes.
- **Monitoring API**: `/events` (keyset-paginated, filtered), `/devices`
  (inventory, profile, operator annotation), `/sensors` (catalogue with live
  availability, CRUD with reasons), `/jobs` (listing, stats, cancel, retry).
  **Simulated data is excluded from every real-network view by default.**

### Fixed

- **Sensor configuration changes never took effect.** The API reloaded the
  sensor manager inline, which reads the database in its own session before the
  request's transaction has committed — so it never saw the row it was meant to
  start. A FastAPI background task does not fix this either: background tasks
  run *before* yield-dependency teardown, which is where the commit happens.
  The API now signals the manager, which re-reads on its own schedule after a
  short debounce, and reconciles on a timer regardless.
- **A completed laboratory scenario reported the whole system as unavailable.**
  Finite sensors now finish in a distinct `COMPLETED` state, which the health
  endpoint treats as healthy along with `STOPPED`. A dashboard that shows red
  for "the thing you asked for finished" teaches people to ignore red.
- **`Secure` session cookies made local development impossible.** The flag now
  follows the environment (on in production, off elsewhere) instead of
  defaulting on everywhere; production still rejects an explicit `false`. The
  previous default meant the workaround for local login was disabling it
  globally.
- **INET columns crashed serialisation.** asyncpg returns `IPv4Address`
  objects; every schema exposing an address now coerces through one shared
  annotated type rather than scattered `str()` calls.
- **A dedupe collision poisoned the caller's transaction.** The enqueue
  savepoint now wraps the `add` as well as the flush, and expunges the doomed
  object so a later autoflush cannot resurrect it.
- **`claim()` returned stale ORM state.** A bulk `UPDATE … RETURNING` left
  objects already in the identity map showing pre-claim values, so a caller
  claiming and failing a job in one session never exhausted its retries.


### Added — M5: detection and risk scoring

- **Canonical observation model** (`nexus/detection/observation.py`): a frozen,
  provenance-carrying projection of `security_events` that every detector
  consumes. It answers, for any observation, which sensor produced it, when, and
  which raw event supports it — as a read-side type rather than a second table,
  because two records of one fact can disagree.
- **Four deterministic detection rules** (`nexus/detection/rules/`):
  `new_device` (novelty decided from `MIN(security_events.id)`, not from a
  column ingestion mutates), `identity_change` (MAC/IP disagreement, in both
  directions), `repeated_anomaly` (by rate *and* by distinct-endpoint
  enumeration) and `unexpected_communication` (monitored source, unmonitored
  destination, port outside the allow-list). Every finding cites its
  observations; nothing is inferred beyond what was recorded.
- **Findings** (migration `0003`): persistent records with a unique
  `fingerprint`, **independent `severity` and `confidence` columns**, structured
  evidence, a written explanation, the rule and rule version, triage state, and
  an `is_simulation` flag that is part of the fingerprint — so a laboratory
  finding can never collapse into a real one.
- **Explainable risk scoring** (`nexus/detection/risk.py`): a weighted sum of
  seven named factors with configurable weights and documented saturation
  points. Every factor is stored with its weight, magnitude, contribution and
  the evidence it came from, including the factors that contributed nothing.
  **No machine learning and no language model** is involved in scoring — a
  security score exists to be argued with, and a learned number cannot be.
- **Risk history** (`device_risk_assessments`): append-only, written only when
  the derivation changes, each row carrying the score it replaced — so "why did
  this go up on Tuesday?" is answerable from the data.
- **Idempotency enforced by the database, not by application checks**:
  `findings.fingerprint UNIQUE`, `PRIMARY KEY (finding_id, event_id)` on the
  evidence link, and a risk score that is recomputed rather than accumulated.
  Re-delivering an analysis job creates no duplicate finding, adds no evidence
  link, and cannot raise a score.
- **Watermark-driven analysis** (`detection.analyze_pending`): bounded batches
  over a monotonic event id, O(1) state, restartable, with a
  `pg_try_advisory_xact_lock` so a second worker returns instead of blocking.
  Ingestion nudges it in the same transaction that stored the events; the
  scheduler runs it every 60 seconds regardless.
- **Detection API**: `/detections` (findings with both axes, filtered,
  simulation excluded by default), `/detections/{id}/evidence` (every linked
  observation with its provenance), `/detections/{id}/status` (triage with a
  required written note, which re-derives the device's risk),
  `/detections/status` (engine state, per-scope detector availability),
  `/detections/config` (read and tune, with optimistic concurrency), and
  `/devices/{id}/risk` plus `/risk/assessments`.
- **Tuning in `app_settings`** under `detection.config`, validated on write and
  on read. An invalid stored document falls back to defaults and reports
  `DEFAULT_AFTER_INVALID_CONFIGURATION` naming the offending field, rather than
  leaving an operator convinced their tuning is in force.
- **Auditing on the existing hash chain**: `DETECTION_FINDING_CREATED`,
  `DETECTION_FINDING_TRIAGED`, `DETECTION_CONFIG_CHANGED` and
  `DEVICE_RISK_CHANGED` (on band changes, not on every point of movement). A
  burst beyond 25 findings in one pass writes one summary entry instead, so a
  flood cannot make the audit log unreadable. No second audit mechanism exists.
- **Honest states**: `NEVER_RUN` / `IDLE` / `BACKLOG` / `STALLED` for the
  analysis pass, `NOT_CONFIGURED` with a remedy per detector per scope,
  `NOT_ASSESSED` versus a score of zero, and `COMPLETE` / `PARTIAL` / `PRUNED`
  for evidence that retention has thinned.

### Fixed

- **Two jobs could race and write a stale risk score.** Found by running the
  system, not by the tests: `detection.analyze_pending` and `risk.rescore_stale`
  both score devices, and on a freshly started deployment both ran within the
  same second. Under `READ COMMITTED` the job that began first saw none of the
  other's freshly-committed findings, computed the no-findings score, and —
  committing last — overwrote the correct score. A device that should have read
  40 read 10. `RiskService.rescore` now takes `SELECT … FOR UPDATE` on the
  device before reading its findings, and both multi-device callers lock in
  device-id order so they cannot deadlock.
  `tests/test_risk.py::TestConcurrentRescoring` reproduces the race and fails
  without the lock.
- **The repeated-anomaly rule could not fire.** Its default categories
  (`ANOMALY`, `SCAN`) match no observation any shipped sensor produces — every
  flow sensor emits `TRAFFIC` — so the rule would have found nothing forever,
  including against the laboratory's own `port_scan` scenario. It now counts two
  ways: a rate condition over categories that are anomalous by nature, and an
  enumeration condition counting distinct `(address, port)` pairs, which is the
  only shape that works for flow data.
- **A finding's observation window could end before it began.** Events arrive in
  id order, not timestamp order, so a batch containing a backdated flow produced
  `last_observed_at < first_observed_at`. The window is now computed with
  min/max over the cited observations. The `CHECK` constraint caught this before
  any bad row was stored.
- **Assessment history recorded ticking rather than movement.** A device under a
  sustained scan accumulated seven consecutive assessments at an identical score
  because each pass moved a timestamp inside a factor's evidence blob. Only the
  arithmetic — code, weight, magnitude, contribution — is now compared.
- **A rejected configuration document reached the client as a 500.** The merge
  raised pydantic's own `ValidationError` rather than the API's envelope.

### Not yet implemented

Threat intelligence (no reputation feed is shipped or consulted), real-time
streaming, the frontend, the laboratory, quarantine, and backup tooling. See
the README for the current list.
