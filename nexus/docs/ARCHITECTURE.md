# NEXUS architecture

This document explains how NEXUS is put together and — more usefully — *why*.
Each section states the problem the subsystem solves, the abstraction chosen,
what happens underneath, the alternatives considered, and the security
implications.

Sections describing subsystems that are not yet built are marked
**NOT IMPLEMENTED** and describe the intended design only.

---

## 1. Shape of the system

```
   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
   │   sensors    │   │   sensors    │   │   sensors    │   NOT IMPLEMENTED
   │   (pcap)     │   │ (discovery)  │   │  (syslog)    │
   └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
          │ raw observations │                  │
          └──────────┬───────┴──────────────────┘
                     ▼
            ┌─────────────────┐
            │  normalisation  │  raw → SecurityEvent            NOT IMPLEMENTED
            └────────┬────────┘
                     ▼
   ┌─────────────────────────────────────────────┐
   │              PostgreSQL                     │  events, devices, detections,
   │  (storage, job queue, pub/sub via NOTIFY)   │  audit, sessions, settings
   └───────┬───────────────────────┬─────────────┘
           │                       │
           ▼                       ▼
   ┌───────────────┐       ┌────────────────┐
   │   workers     │       │   HTTP API     │  FastAPI, /api/v1
   │  detection,   │       │  auth, RBAC,   │
   │  risk, intel, │       │  validation,   │
   │  retention    │       │  SSE stream    │
   └───────────────┘       └───────┬────────┘
     NOT IMPLEMENTED               │
                                   ▼
                          ┌────────────────┐
                          │   frontend     │  React SPA         NOT IMPLEMENTED
                          └────────────────┘
```

Every arrow is an explicit interface. No subsystem reaches into another's
internals; they communicate through the database, through service classes with
declared signatures, or over HTTP.

### Why PostgreSQL for storage, queue, *and* pub/sub

The obvious alternative is PostgreSQL + Redis + Celery: Redis for the queue and
for fan-out, Celery for workers. That is the conventional stack, and it is
right at a scale NEXUS will not reach.

What was chosen instead:

- **Queue:** a `jobs` table consumed with `SELECT … FOR UPDATE SKIP LOCKED`.
  This is a genuine, race-free work queue — `SKIP LOCKED` lets N workers pull
  disjoint rows without blocking each other.
- **Fan-out:** PostgreSQL `LISTEN`/`NOTIFY` to push new events to API processes,
  which relay them to browsers over Server-Sent Events.

The tradeoffs, stated plainly:

| | Postgres-only | + Redis/Celery |
|---|---|---|
| Operational surface | One service to install, back up, and monitor | Three |
| Job durability | Transactional with the data that created the job | Redis persistence is separate and weaker |
| Throughput ceiling | Thousands of jobs/minute — ample for a home network | Far higher |
| `NOTIFY` payload limit | 8 KB, so we send ids and let clients fetch | No such limit |
| Failure modes to learn | Fewer | More |

For a self-hosted appliance monitoring one network, "one service" is worth more
than a throughput ceiling nobody will hit. The queue lives behind an interface,
so replacing it later does not touch calling code.

---

## 2. Configuration — `nexus/core/config.py`

**Problem.** Subsystems need facts they cannot discover (database location,
which networks the operator owns). Reading `os.environ` all over the codebase
makes those requirements invisible until something fails in production.

**Abstraction.** One immutable, validated `Settings` object, built once at
startup from the environment.

**Underneath.** pydantic-settings reads `NEXUS_*` variables, coerces types, runs
field validators, then runs a model validator for cross-field rules. Failure
raises `ConfigurationError` carrying a message that names the variable and how
to fix it; `main` prints it and exits `78` (`EX_CONFIG`).

**Alternatives.** A YAML/TOML file (better for large nested config; worse for
containers and secret injection); a config service (overkill). Environment
variables were chosen for 12-factor compatibility, with a `.env` file for
developer convenience only.

**Security implications.**

- Secrets are `SecretStr`, so `repr()` in a log, a traceback, or a debugger
  cannot leak them. Reading one requires `.get_secret_value()`, which is
  greppable in review.
- Production enforces rules development does not: a real signing key, secure
  cookies, no wildcard CORS. The check runs in the process, because a
  deployment checklist is not executed by anything.
- `monitored_networks` is the platform's safety boundary. It defaults to empty,
  and empty means *nothing* is monitored, so an operator who has not declared
  their network cannot act on it. `0.0.0.0/0` is rejected outright.

---

## 3. Logging — `nexus/core/logging.py`

**Problem.** "Show me everything that happened while handling request 7f3a"
cannot be answered by free-text log lines.

**Abstraction.** JSON Lines records with stable field names, plus a correlation
id carried implicitly through the call stack.

**Underneath.** `contextvars.ContextVar` is the async-aware equivalent of
thread-local storage: each request's task gets its own copy, so any code it
calls — however deep — sees the right request id without it being threaded
through every function signature. A middleware sets it once per request; a
custom `logging.Formatter` merges it into every record.

**Alternatives.** `structlog` (excellent, and a dependency plus an idiom to
learn); passing a logger object down the call stack (explicit, invasive, and
third-party library logs bypass it entirely). The stdlib was chosen because
records from *any* library then flow through the same formatter and the same
redaction.

**Security implications.** Redaction happens in the formatter — the single
point every record passes through — in two layers:

1. **By key.** A field whose name contains `password`, `token`, `secret`,
   `cookie`, `authorization`… is replaced with `[redacted]`. Deliberately broad:
   a false positive costs one unreadable debug field; a false negative writes a
   credential to disk.
2. **By pattern in free text.** A secret embedded in a sentence
   (`could not connect to postgres://user:hunter2@db/nexus`) is invisible to
   key-based redaction. URL credentials, `Bearer` tokens, and `key: value`
   secrets are scrubbed from string values.

Redaction is depth-limited, because a logger that overflows the stack on a
pathological payload is a denial of service in the observability path — the one
component that must survive the incident it is describing.

---

## 4. Errors — `nexus/core/errors.py`, `nexus/api/errors.py`

**Problem.** Domain code that raises `HTTPException` can only be called from
HTTP. And unhandled exceptions leaking to clients hand attackers reconnaissance.

**Abstraction.** An `AppError` hierarchy describing *what went wrong*
(`NotFound`, `PermissionDenied`, `NotConfigured`, `DependencyUnavailable`,
`OperationNotPermitted`), and one adapter in the API layer that maps them to
status codes and a single JSON envelope:

```json
{"error": {"code": "NOT_FOUND", "message": "…", "details": {}, "request_id": "…"}}
```

**Notable distinctions.**

- `NotConfigured` (503) vs `DependencyUnavailable` (503): "you never set this
  up" and "you set it up and it is broken" have different remedies, so the UI
  shows them differently.
- `OperationNotPermitted` (403) vs `PermissionDenied` (403): the first is a
  safety invariant no role can lift (target outside your networks); the second
  is about who is asking.

**Security implications.** Anything that is not an `AppError` becomes a generic
`INTERNAL_ERROR` carrying only a correlation id. Stack traces, SQL fragments,
and paths go to the log; the client gets an id to quote. Validation errors are
stripped of pydantic's `input` field, which for a login request is the password.

---

## 5. Authorization — `nexus/core/rbac.py`

**Problem.** Conflating authentication with authorization produces the classic
bug where every logged-in user can reach an admin endpoint.

**Abstraction.** `Role → frozenset[Permission]`. Permissions are verbs on
resources (`devices:quarantine`, `rules:write`), and endpoints declare the
permission they require — never the role.

**Why a matrix in source rather than a database table.** A database-backed
permission table allows runtime-defined roles, but the effective policy becomes
invisible in code review and a single edited row silently grants privileges.
For a single-tenant appliance with three roles, a matrix that is reviewable in a
diff, testable without a database, and unchangeable without a deploy is the
safer trade. The shape (role resolves to a permission set) matches what a
database would store, so the migration path stays open.

**Security implications.** An unrecognised role resolves to *no* permissions
rather than raising or defaulting — failing closed. `SENSITIVE_PERMISSIONS`
lives beside the matrix and drives both the UI's typed-confirmation requirement
and the API's mandatory-reason rule, so the two cannot drift apart.

---

## 6. Database access — `nexus/db/`

**Problem.** Connections are expensive (TCP + TLS + a forked backend process);
opening one per request caps throughput and lets a traffic spike kill the
database.

**Abstraction.** A `Database` object owning an async engine and a pool, handing
out sessions through an async context manager that commits on success and rolls
back on any exception.

**Underneath.** `pool_size` is the steady-state connection count,
`max_overflow` the emergency headroom, `pool_timeout` how long a request waits
before failing fast. `pool_pre_ping` costs one round trip on checkout and saves
you from a socket a firewall closed while idle. A server-side
`statement_timeout` ensures no runaway query pins a connection forever.

**Unit of work.** Callers never call `commit()`. The session context manager
does, once, at the end. That is what makes "an action and its audit entry are
one transaction" a structural guarantee rather than a convention someone can
forget: if the action rolls back, so does its audit row.

**Degraded start.** If the database is unreachable at boot, the process still
starts, reports itself *not ready*, and returns `DEPENDENCY_UNAVAILABLE` from
data endpoints. An orchestrator restarting the app does not fix a broken
database — it just adds an outage. Readiness is re-evaluated on every probe, so
recovery needs no restart.

**Alternatives.** Sync SQLAlchemy with a thread pool (simpler, but blocks the
event loop and fits poorly with long-lived SSE connections); raw asyncpg (fast,
but no unit of work and hand-written SQL everywhere).

---

## 7. Schema design

Two primary-key strategies, chosen per table:

- **Entities** (`users`, and later devices, rules, sensors) use UUIDv4. They
  appear in URLs; sequential integers would let any authenticated viewer count
  objects and probe for them.
- **Append-only firehose tables** (`audit_events`, and later `security_events`)
  use `BIGINT GENERATED ALWAYS AS IDENTITY`. Random UUIDs scatter inserts across
  a B-tree, dirtying many pages per insert; a monotonic key appends at the
  right-hand edge. The audit log additionally *requires* a total order, because
  its tamper-evidence is a hash chain over consecutive rows.

Constraint naming follows a metadata convention, so every constraint name is a
deterministic function of table and columns. Without it, PostgreSQL invents
names that differ between databases and Alembic generates migrations that try
to drop constraints that do not exist under that name.

Enumerations are `VARCHAR` + `CHECK` rather than native PostgreSQL enums:
`ALTER TYPE` on a hot enum column is a migration hazard, and the check
constraint gives the same guarantee with none of the pain. The database
enforces the vocabulary independently of Python, so a maintenance script writing
raw SQL cannot invent a role.

---

## 8. Audit log — `nexus/db/models/audit.py`

**Problem.** After an incident, "who quarantined this device and why" must be
answerable, and the answer must be hard to alter.

**Abstraction.** Append-only table where each row stores
`entry_hash = SHA256(canonical(row) ‖ prev_hash)` — a hash chain. Editing or
deleting row *N* invalidates every hash after it, so verification finds the
exact break.

**What this does not defend against.** An attacker with database write access
can recompute the chain from the edited row forward. The chain proves
*consistency*, not *authenticity*. Defending against that requires an anchor
outside the database: an append-only mirror on a separate filesystem, or
periodic checkpoint hashes shipped to a remote log. Those are planned, and
until they exist this document says so rather than implying more protection
than the code provides.

**Design details that matter.**

- `actor_username` is a *copy*, not a join. Deleting a user must not erase what
  they did, and a rename must not rewrite history. The foreign key is
  `ON DELETE SET NULL` for the same reason.
- `GENERATED ALWAYS` on the id means even a direct SQL `INSERT` cannot choose
  an id, so nobody can wedge a forged row between two existing links.
- Appends will be serialised with a PostgreSQL advisory lock, because two
  concurrent transactions reading the same `prev_hash` would fork the chain.
  Audit volume is low; serialising it is cheap.

---

## 9. HTTP layer — `nexus/api/`

Middleware order, outermost first:

1. **RequestContext** — assigns/validates the correlation id, times the request,
   emits the access log. Outermost so failures in other middleware are still
   correlated. An inbound `X-Request-ID` is honoured only if it looks like one
   we would have generated; echoing arbitrary client input into log lines is log
   injection.
2. **SecurityHeaders** — CSP, `nosniff`, `X-Frame-Options: DENY`,
   `Referrer-Policy`, `Permissions-Policy`, and HSTS in production only.
3. **CORS** — only when origins are configured; credentials are allowed only
   with an explicit origin list, never `*`.
4. **BodySizeLimit** — checks `Content-Length` up front *and* counts streamed
   chunks, because a chunked request has no `Content-Length` and could otherwise
   stream gigabytes into memory.

Health endpoints are split three ways because "is it healthy" is three
questions: `/health/live` (is the process up — no dependency checks, so a
database outage cannot cause a restart storm), `/health/ready` (should it
receive traffic — 503 when not), and `/health` (what specifically is wrong —
always 200, so "NEXUS says the database is down" is distinguishable from "NEXUS
is unreachable"). All three are unauthenticated, so their `detail` strings are
scrubbed before they leave the process.

---

## 10. Testing strategy

Tests run against a **real PostgreSQL database**. The schema uses JSONB, INET,
`GENERATED ALWAYS AS IDENTITY`, and `ON DELETE` behaviour — none of which SQLite
implements. A suite that passes against a different database than production
runs tells you nothing about production.

The schema is created by running the real migrations, so every test run also
exercises the migration path. Isolation is by truncation (microseconds) rather
than by re-running migrations per test (minutes). Tests that talk to the
database directly get a session bound to a transaction that is always rolled
back, so a test that deliberately triggers an `IntegrityError` — proving a
constraint works — cannot poison teardown.

Failure paths are tested as first-class citizens: database down, credentials in
error strings, oversized bodies, forged request ids. Anyone can make a health
check return 200 when everything works.

---

## Change log for this document

- **M1** — established sections 1–10 with the foundation subsystems.
