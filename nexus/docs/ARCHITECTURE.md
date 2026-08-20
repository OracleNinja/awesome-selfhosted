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

## 10. Authentication and sessions — `nexus/services/auth.py`

**Problem.** Prove who a caller is on every request, and be able to stop
believing them at any moment.

**Abstraction.** Opaque server-side sessions: a random 256-bit token in an
`HttpOnly` cookie, its SHA-256 in `user_sessions`.

**Alternative considered: JWT.** Self-contained, so validation needs no
database round trip — the reason stateless APIs prefer them. The cost is that
they cannot be revoked: a stolen token remains valid until it expires, and
"sign out everywhere" needs a denylist, which reintroduces the lookup you were
avoiding. On a console that can quarantine devices, immediate revocation is
worth one indexed query per request.

**Underneath.** Login hashes the cookie value and probes a unique index, then
checks four things: revoked, absolutely expired, idle-expired, account still
active. `last_seen_at` is only written when it is more than a minute stale —
otherwise every API call would be a database write.

**Why the hash is fast (SHA-256) but passwords are slow (Argon2id).** Slow
hashing exists to make *guessing* expensive, and guessing only matters when the
input is guessable. A password is; 256 bits of CSPRNG output is not. Paying
50 ms of Argon2 per request to look up a session would be a self-inflicted
denial of service.

**Security implications.** Documented in full in SECURITY.md §2: uniform
failure responses, dummy hashing on the not-found path so timing does not leak
account existence, durable per-account lockout, and a per-process rate limit
that is explicitly labelled best-effort.

---

## 11. The commit-boundary exception

The unit-of-work rule (§6) says callers never commit. Authentication failure is
the one deliberate exception, and it is worth understanding why.

A failed login raises, and an exception rolls back the request's transaction —
which would erase the incremented failure counter *and* the audit row recording
the attempt. The evidence of an attack would be destroyed by the mechanism that
rejects it. So the failure path commits explicitly before raising. It is safe
precisely because nothing else is pending in that transaction at that point.

The same reasoning applies to audited permission denials.

---

## 12. Sensors — `nexus/sensors/`

**Problem.** Observe a network without inventing anything.

**Abstraction.** A driver interface with four methods: check availability,
yield observations, stop, describe. Everything downstream sees one shape and
never learns which driver produced an event.

**The drivers that exist today**

| Driver | What it actually does | Privilege |
|---|---|---|
| `arp_discovery` | Reads `/proc/net/arp` — the kernel's own neighbour cache | none |
| `syslog` | UDP listener parsing RFC 3164 and RFC 5424 | none (port >1024) |
| `packet_capture` | `AF_PACKET` raw socket, frames folded into flows | `CAP_NET_RAW` |
| `lab_synthetic` | Generates laboratory scenarios — **the only source of fabricated data** | none |

**Honesty enforced structurally, not by convention.** Two mechanisms:

* `check_availability()` runs *before* a sensor starts and returns
  `NOT_AVAILABLE` (this host cannot) or `NOT_CONFIGURED` (you have not set it
  up) with a human remedy — the exact `setcap` command, the list of real
  interface names. A sensor that cannot run says so, instead of reporting no
  events, which would read as "your network is quiet".
* `produces_simulated_data` is a **class attribute**, and ingestion stamps
  `is_simulation` from the driver class rather than from the observation or the
  sensor row. A laboratory driver therefore cannot produce data that looks
  real, even if a caller passes the wrong argument.

**Packet capture, underneath.** `socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL))`
asks the kernel for a copy of every frame on an interface. That is why it needs
a capability: a process that can read every frame can read every unencrypted
password on the segment. NEXUS asks for `CAP_NET_RAW` alone rather than running
as root — the whole point of capabilities — and the socket is never written to.
Frames are folded into flows (source, destination, protocol, port, counters)
because one event per packet would be hundreds of millions of rows a day and
useless. Payload bytes are never stored.

**Parsing is hostile-input handling.** Every parser checks its length before
unpacking and returns `None` on anything unrecognised. A malformed frame is a
discarded frame, never an exception — a crash in the sensor is a monitoring
outage, which is precisely what an attacker would want.

**Supervision.** Each sensor runs in its own task; a crash restarts it with
exponential backoff, and after a burst of failures it is parked in `FAILED`
with the reason on its row. Silently retrying forever is how an operator ends
up believing they are monitored when they are not.

**Configuration changes.** The manager owns the timing. An API handler that
changes a sensor *signals* the manager, which re-reads the table in its own
session after a short debounce, and also reconciles on a 30-second timer.
Reloading inline reads the database before the request's transaction commits
and misses the row that was just written — and a FastAPI background task does
not fix it either, because background tasks run *before* yield-dependency
teardown, which is where the commit happens. This was a real bug, caught by
running the thing.

---

## 13. Ingestion — `nexus/services/ingest.py`

The seam between "what a sensor saw" and "what the system reasons about". Four
jobs: deduplicate, resolve the device, normalise, stamp provenance.

**Deduplication** uses a UUIDv5 derived from the observation's own content, so
a sensor restarting and re-reading the same kernel table produces the same ids
and the unique index discards them. Without it, every restart re-imports
history and inflates every count, rate, and detection threshold downstream.

**Device identity** is MAC first, IP second. A MAC survives a DHCP lease
change, so keying on it keeps one device as one device; keying on IP would make
every lease renewal look like a new host. Resolution is an upsert, because two
workers can ingest observations about the same device concurrently — and
devices with no visible MAC are covered by a *partial* unique index on the
address.

**A device is an inference, not a fact.** Phones randomise MACs, DHCP reassigns
addresses, NAT hides many hosts behind one. `vendor` stays NULL unless a real
OUI database is loaded, because a guess would be indistinguishable from
evidence. `risk_score` stays NULL until the risk engine has run, because "not
assessed" is a different claim from "assessed and found safe".

---

## 14. The job queue — `nexus/db/models/job.py`, `nexus/services/jobs.py`

**Why not Redis/Celery.** The decisive property is **transactional enqueue**:
storing an event and enqueuing the work it triggers happen in one transaction,
so there is no window where the data exists and the work was never scheduled.
With an external broker that gap needs an outbox table and idempotency keys —
more machinery than the broker saved.

**The claim.** One statement: select eligible rows `FOR UPDATE SKIP LOCKED`,
mark them RUNNING, return them. `SKIP LOCKED` is what makes it a queue rather
than a bottleneck — a second worker skips rows the first has locked instead of
blocking behind them, so workers scale with no coordination. Without it they
serialise; without `FOR UPDATE` they double-process.

**Delivery is at least once.** A worker can do the work and die before
recording success. Handlers must therefore be idempotent, and every handler
says in its docstring how it achieves that. Exactly-once is not available: the
work and the record of the work are two systems.

**Recurring work without a scheduler daemon.** Each schedule entry floors the
clock into a bucket and enqueues with `dedupe_key = f"{kind}|{bucket}"`. Every
worker computes the same key and tries; the partial unique index means exactly
one insert wins. No leader election, no lock, no single point of failure.

**Transaction shape in the worker.** The handler's work and the job's
completion commit *together*, so a crash between them is impossible. A failure
takes the opposite path: the handler's transaction is rolled back, and the
failure is recorded in a second, fresh transaction — recording it in the
rolled-back one would roll back the failure too, and the job would look as
though it had never run.

---


## 15. Testing strategy

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

## 16. Detection — `nexus/detection/`

### The canonical observation model

Detection rules consume `Observation` (`nexus/detection/observation.py`), a
frozen dataclass projected from a `security_events` row. It carries the
normalised network fields plus a `Provenance` record that answers, for any
observation:

| Question | Field |
|---|---|
| Which sensor produced this? | `provenance.sensor_name` / `sensor_id` / `driver` |
| When was it produced? | `provenance.occurred_at` (sensor clock) / `received_at` (ours) |
| Which raw event supports it? | `provenance.event_id` / `event_uid` / `has_raw` |
| Is it real or laboratory data? | `provenance.is_simulation` |

This is a **read-side projection, not a second table**. `security_events` (M4)
already stores every one of those fields; copying them would create two records
of one fact that can disagree, double the storage of the highest-volume table,
and need a synchronisation job whose failure mode is silent divergence.

The type is frozen because detectors run in sequence over the same batch. If one
could mutate an observation, the next rule's conclusion would depend on
execution order and the pipeline would stop being deterministic.

### The analysis pass

```
watermark ─▶ fetch a bounded batch of security_events (id > watermark, in id order)
                ├─ project to Observations
                ├─ split by scope: real | simulation      (never mixed)
                ├─ run each available detector per scope
                ├─ persist candidates by fingerprint      (INSERT … ON CONFLICT)
                ├─ link evidence                          (PK (finding_id, event_id))
                ├─ recompute risk for touched devices     (pure function)
                ├─ audit what changed                     (existing hash chain)
                └─ advance the watermark
                         │
                         ▼  one transaction, committed by the worker
```

**Why a watermark, not a queue of event ids.** Enqueuing each event would make
the job table larger than the event table, and would make cross-event rules —
which must count occurrences inside a window — depend on job ordering. A
watermark over a monotonic id gives ordered, bounded, restartable batches with
O(1) state. It cannot skip an event, because ids come from a single
`GENERATED ALWAYS AS IDENTITY` sequence and the pass advances only to the
highest id it actually read.

**Concurrency.** The pass takes a transaction-scoped advisory lock, and takes it
with `pg_try_advisory_xact_lock`. A second worker finding the lock held returns
immediately and its job succeeds having done nothing: blocking would tie up a
worker to do work already in progress.

**Scope isolation is structural.** Real and simulated observations are separate
passes with separate history queries, so no rule can correlate a laboratory
event with a real one even by mistake. The finding fingerprint includes the
scope, so the two can never collapse into one row.

### The four rules

| Rule | Fires on | Severity | Confidence |
|---|---|---|---|
| `new_device` | The lowest `security_events.id` mentioning a device falls inside this batch | MEDIUM | 90 with a MAC, 60 by IP only |
| `identity_change` | A (MAC, IP) pair disagrees with the last pre-batch observation | HIGH for `IP_REASSIGNED`, MEDIUM for `MAC_MOVED` | 35, or 60 when the change is abrupt (≤300 s) |
| `repeated_anomaly` | *Rate*: same kind, N times in a window. *Enumeration*: N distinct endpoints in a window | up to HIGH (rate), MEDIUM (enumeration) | scales with how far past the threshold |
| `unexpected_communication` | Monitored source → non-monitored destination on a port outside the allow-list | MEDIUM | 70 |

`new_device` decides novelty from the **evidence** (`MIN(security_events.id)` for
the device), not from `devices.first_seen_at`, which ingestion maintains and
which moves under concurrent upserts. Event ids never change, so the same
question asked twice gives the same answer.

`repeated_anomaly` counts two ways because sensors produce two kinds of
evidence. A syslog line saying "authentication failed" is self-describing, so
forty of them in ten minutes is a rate signal. A packet-capture sensor emits
`TRAFFIC` records where no individual flow is anomalous and the whole signal is
in the spread — so that half counts *distinct `(address, port)` pairs*. Counting
raw flows there would report that a busy device is busy, which is why `TRAFFIC`
is excluded from the rate condition's default categories.

`unexpected_communication` reports `NOT_CONFIGURED` for the real-network scope
until `NEXUS_MONITORED_NETWORKS` is set. With no declared networks,
`Settings.is_monitored_address` returns `False` for everything (it fails closed,
by design), so the rule would see all traffic as external-to-external and fire on
everything or nothing. Both would be lies about the network, so it declines to
run and says why.

### Severity and confidence are separate columns

* **Severity** — how bad this is *if the conclusion is correct*.
* **Confidence** — how likely it is that the conclusion is correct.

`identity_change` is the clearest case: an address answering for a different MAC
is the observable signature of ARP spoofing (HIGH), and is also exactly what a
DHCP reassignment looks like (confidence 35). Folding those into one number at
detection time destroys the distinction an analyst uses to decide what to look
at first, so the schema, the scoring model and the API keep them apart.

---

## 17. Risk scoring — `nexus/detection/risk.py`

```
score = clamp(round(sum of (weight_f * magnitude_f)), 0, 100)
```

Seven named factors, each normalised to 0–1 by a documented saturation value and
multiplied by a configurable weight:

| Factor | Default weight | Saturates at |
|---|---|---|
| `SEVERITY_WEIGHTED_FINDINGS` | 45 | 2.0 severity×confidence units |
| `RECENT_ACTIVITY` | 15 | decays to 0 at 72 h |
| `IDENTITY_INSTABILITY` | 15 | 2 identity-change findings |
| `EXTERNAL_EXPOSURE` | 15 | 3 distinct unexpected endpoints |
| `ANOMALY_VOLUME` | 10 | 2 repeated-anomaly findings |
| `DEVICE_NOVELTY` | 10 | first seen within 24 h |
| `OPERATOR_TRUST` | **−30** | device marked trusted |

Every factor is stored with its code, weight, magnitude, contribution and the
evidence it came from — including the ones that contributed nothing, because
"considered and absent" is information. The positive weights sum to more than
100 deliberately: a device that is bad in every dimension saturates rather than
requiring each factor to be scaled down.

**No model, and that is a design decision.** A security score exists to be
argued with. An operator must be able to ask why a device is 72, get an answer
in terms of the evidence, change a weight and see the number move predictably. A
learned model gives a number that is at best correlated with something and at
worst unfalsifiable. Nothing in the scoring path calls a model of any kind.

**`UNKNOWN` is not zero.** A device the engine has never assessed has
`risk_score = NULL` / `risk_level = 'UNKNOWN'`. A device assessed with no active
findings has `risk_score = 0` / `risk_level = 'LOW'`. Different claims, kept
apart in the schema, the API and (later) the UI.

### Configuration

Rule parameters and weights live in one `app_settings` row under
`detection.config`, validated by pydantic on **write and on read**. One document
rather than one row per knob, because a risk model is only coherent as a whole
and a half-applied change produces scores that mean nothing; the table's existing
`version` column makes concurrent edits a `409` rather than a silent overwrite.

If the stored document is invalid — a bad migration, a restore, someone with
`psql` — the loader falls back to the documented defaults and **reports
`DEFAULT_AFTER_INVALID_CONFIGURATION`** with the offending field. Silently using
defaults would leave an operator convinced their tuning was in force.

---

## 18. Idempotency, and why it is not application logic

Job delivery is at-least-once (§14), so every part of this pipeline is re-run in
normal operation. Four mechanisms make that safe, and three of them are database
constraints rather than checks in Python — because a check and a write in two
statements is a race, and the loser's write still lands.

1. **`findings.fingerprint UNIQUE`.** A candidate's identity is a digest of what
   was detected: detector, rule version, scope, and the specific device,
   transition or endpoint. Re-analysis re-derives the same fingerprint, and the
   `INSERT … ON CONFLICT DO UPDATE` becomes an update of the observation window.
   Two workers reaching the statement at once produce one row and one update.
2. **`PRIMARY KEY (finding_id, event_id)` on the evidence link.** An observation
   supports a finding at most once. `observation_count` is incremented only by
   the row count of an `ON CONFLICT DO NOTHING` insert, so a replayed pass adds
   zero and no evidence-derived number can drift upward.
3. **Risk is recomputed, never accumulated.** There is no `score += …` anywhere;
   the score is a pure function of the device's current active findings. This is
   what makes "a retry must not repeatedly increase risk" true structurally
   rather than by a guard.
4. **Time enters the model only in whole hours.** The recency factor decays with
   the age of the newest finding, floored to hours. Without that, two runs a
   second apart would differ in the last decimal and append an assessment row
   every time; with it, unchanged input produces an identical result and nothing
   is written.

### `SELECT … FOR UPDATE` on the device before scoring

Found by running the system, not by the tests. `detection.analyze_pending` and
`risk.rescore_stale` both score devices, and on a freshly started deployment
both ran within the same second. Under `READ COMMITTED` each transaction reads
its own snapshot: the job that began first saw *none* of the other's
freshly-committed findings, computed the no-findings score, and — committing
last — wrote that stale score over the correct one. A device that should have
read 40 read 10.

`RiskService.rescore` now locks the device row before reading its findings. The
statement that takes the lock also takes a fresh snapshot, so the second writer
sees the first's work instead of racing it. Both multi-device callers lock in
device-id order, so they cannot deadlock by taking the same locks in opposite
orders. `tests/test_risk.py::TestConcurrentRescoring` reproduces the original
race and fails without the lock.

### Evidence outlives events

Event retention prunes `security_events`, and the evidence link is
`ON DELETE CASCADE`. A finding therefore keeps a bounded snapshot of its
observations in `evidence` plus a monotonic `observation_count`; comparing that
count with the links still present is how the API reports `COMPLETE`, `PARTIAL`
or `PRUNED`. A console that showed a thinned finding as fully supported would be
claiming evidence it cannot produce.

---

## 19. Honest states in detection

| State | Means | Never rendered as |
|---|---|---|
| `analysis.state = NEVER_RUN` | No pass has completed | "no findings" |
| `analysis.state = IDLE` | Every stored event analysed — including after a finite laboratory scenario finishes | a fault |
| `analysis.state = BACKLOG` | Events waiting, a pass ran recently | healthy-and-idle |
| `analysis.state = STALLED` | Events waiting, no pass in 5 minutes — usually no worker is running | healthy |
| detector `NOT_CONFIGURED` | A prerequisite is missing; `remedy` says what to do | an empty result |
| detector `DISABLED` | Turned off in configuration | "found nothing" |
| `risk.state = NOT_ASSESSED` | The engine has not scored this device | a score of zero |
| `weights_source = DEFAULT_AFTER_INVALID_CONFIGURATION` | Stored tuning was rejected | "your settings are active" |
| `evidence_state = PARTIAL` / `PRUNED` | Retention removed some observations | fully-supported |
| `is_simulation = true` | Derived from laboratory data | an observation of your network |

Findings derived from simulation are excluded from every real-network view by
default, exactly as simulated events are.

---

## Change log for this document

- **M1** — established sections 1–9 and the testing strategy.
- **M2** — added §10 (sessions) and §11 (the commit-boundary exception).
- **M4** — added §12 (sensors), §13 (ingestion) and §14 (the job queue).
- **M5** — added §16 (detection), §17 (risk scoring), §18 (idempotency and the
  device-lock defect found in live running) and §19 (honest states).
