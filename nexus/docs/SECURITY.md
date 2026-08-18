# Security

This document describes the security properties NEXUS actually has today, the
attacks each control is meant to stop, and — just as important — what each
control does *not* protect against. A control you misunderstand is worse than
no control, because you stop looking.

Sections describing unbuilt subsystems say **NOT IMPLEMENTED** rather than
describing intent as if it were fact.

---

## 1. Threat model

NEXUS runs on a network the operator owns, holds a record of everything that
happens on that network, and can act on devices. That makes it a high-value
target in its own right: an attacker who owns NEXUS learns the shape of the
network, can hide their own activity from its detections, and can use its
response capability against the operator.

**In scope**

| Adversary | Capability assumed |
|---|---|
| Remote unauthenticated attacker | Can reach the HTTP port; may know the software |
| Malicious LAN device | Sends data that NEXUS ingests; controls its own traffic |
| Authenticated low-privilege user | Valid VIEWER or OPERATOR session |
| Attacker with a stolen session cookie | Full use of one session, no password |
| Attacker with a stolen database dump | Offline access to all stored data |

**Out of scope** (stated so the boundary is explicit)

- An attacker with root on the NEXUS host. They can read the configuration,
  the signing key, and the database. Nothing in the application can defend
  against this; host hardening is the control.
- An attacker with write access to the database. They can rewrite the audit
  chain (see §6).
- Physical attacks, supply-chain compromise of PyPI, and hypervisor escape.

---

## 2. Authentication

**Passwords** are stored as Argon2id verifiers, never as passwords, and never
reversibly encrypted. Argon2id is memory-hard: each guess must allocate and
randomly traverse a large block of memory, which is the one resource a GPU
farm cannot cheaply parallelise. Defaults follow the OWASP recommendation
(64 MiB, 3 iterations, parallelism 2) and are configurable, because the right
cost rises with hardware.

Cost parameters are encoded in each stored hash, so raising them does not lock
anyone out: old hashes still verify, and an account is transparently upgraded
at its next successful login — the one moment the plaintext is legitimately
available.

Hashing runs in a worker thread. On an async server, tens of milliseconds of
CPU inside a coroutine stalls *every* concurrent request.

**Against online guessing**, four layers:

1. Every failure — unknown user, wrong password, disabled account, locked
   account — returns the same status and the same message. Any difference is a
   free account-enumeration oracle.
2. When the username does not exist, a dummy Argon2 verification burns
   equivalent CPU, so the two cases cannot be told apart with a stopwatch.
3. A durable per-account lockout (counter on the user row) after
   `NEXUS_LOGIN_MAX_FAILED_ATTEMPTS`. It survives restarts and is shared by
   every worker process, so an attacker cannot reset their budget.
4. A per-process rate limit on the login endpoint, keyed on address *and*
   username. **This layer is best-effort**: its state is in memory, so two
   worker processes each allow the budget and a restart clears it. It is a
   speed bump; the lockout is the control.

**Sessions** are opaque server-side tokens, not JWTs. A JWT needs no database
lookup, but cannot be revoked — a stolen token stays valid until it expires. On
a console that can quarantine devices, an operator must be able to kill a
session immediately, so revocability wins.

- 256 bits from the OS CSPRNG. The database stores only the SHA-256, so a
  database dump does not hand over live sessions. A fast hash is correct here
  because the input has no guessable structure.
- Bounded by two clocks: an absolute expiry (default 12 h) and an idle timeout
  (default 60 min).
- A fresh session is issued at every login, which defeats session fixation.
- Revoked on logout, password change, password reset, role change, and
  deactivation.

**Cookie flags:** `HttpOnly` (an XSS bug cannot read the session), `Secure`
(refused in production if disabled), `SameSite=Strict`, `Path=/`.

**Bootstrap.** There is no default account. The first administrator is created
by `python -m nexus.cli create-admin` by someone with shell access. Passwords
are typed interactively or generated and printed once — never accepted as a
command-line argument, because arguments are visible in `ps` to every user on
the host.

---

## 3. Authorization

Three roles resolve to explicit permission sets (`nexus/core/rbac.py`).
Endpoints declare the permission they need, never the role.

- `VIEWER` — read only.
- `OPERATOR` — read, triage detections, annotate devices, quarantine, run lab
  scenarios. Cannot change detection logic, integrations, users, or evidence.
- `ADMIN` — everything.

Properties worth knowing:

- An unrecognised role resolves to **no** permissions. Fails closed.
- Denials are audited. A VIEWER repeatedly probing admin endpoints is signal,
  and a system that records only successes cannot show it to you.
- Role changes revoke the user's sessions, so a demotion takes effect
  immediately rather than whenever their session happens to expire.
- An admin cannot demote or deactivate themselves, and the last active admin
  cannot be removed — otherwise the deployment becomes unadministrable.

---

## 4. Cross-site request forgery

Cookies are attached by the browser based on a request's *destination*, so a
malicious page an operator visits can make their browser issue authenticated
requests to NEXUS. Three layers:

1. `SameSite=Strict` — the browser does not attach the cookie to cross-site
   requests at all. This alone defeats the classic attack in current browsers.
2. A CSRF token issued at login, held in memory by the SPA, echoed in the
   `X-CSRF-Token` header, and compared against a **hashed** copy stored with the
   session. An attacking page cannot read it, and cannot send a custom header
   cross-origin without a CORS preflight that NEXUS does not grant.
3. Constant-time comparison, so the check is not a timing oracle.

Enforced inside the identity dependency, so an authenticated write endpoint
cannot exist without it. `GET`/`HEAD`/`OPTIONS` are exempt — which is why no
state-changing operation may ever be exposed as a `GET`.

---

## 5. Injection and untrusted input

**SQL injection.** All queries go through SQLAlchemy with bound parameters.
There is no string-concatenated SQL anywhere in the codebase, including the
audit filters, which are the most parameter-heavy queries in the system.

**Cross-site scripting.** The API returns JSON with `nosniff`, so a response
cannot be re-interpreted as HTML. A Content-Security-Policy of `default-src
'self'` with no `unsafe-inline` for scripts means injected markup still cannot
execute. Attacker-controlled strings (user agents, hostnames, event payloads)
are truncated on write and are never rendered as markup.

**Request-size exhaustion.** Bodies are capped before buffering, checked both
by `Content-Length` and by counting streamed chunks — a chunked request has no
`Content-Length` and could otherwise stream unbounded data into memory.

**Log injection.** An inbound `X-Request-ID` is used only if it matches the
shape of an id we would generate; otherwise it is replaced. Otherwise a client
could embed newlines and forge log entries.

**Source-address spoofing.** `X-Forwarded-For` is ignored unless
`NEXUS_TRUSTED_PROXY_HOPS` is set, and then only the hop a trusted proxy
actually observed is used. Trusting it blindly would let anyone write any
address they liked into the audit log.

**SSRF, command injection, path traversal, deserialization.** NEXUS currently
makes no outbound requests on behalf of user input, executes no subprocesses,
opens no user-named files, and deserialises nothing but JSON through pydantic.
These become live concerns with threat intelligence (outbound), quarantine
(subprocess/API), and evidence export (files); each will be documented here
when built.

---

## 6. Audit log integrity

Every security-sensitive action writes an audit row **in the same transaction
as the action**, so an action cannot be committed without its evidence — and a
rolled-back action cannot leave a row claiming it happened.

Rows form a hash chain: `entry_hash = SHA256(canonical(row) ‖ prev_hash)`.
Editing or deleting a row invalidates every hash after it.
`POST /api/v1/audit/verify` and `python -m nexus.cli verify-audit` walk the
chain and report the first break.

Supporting details: ids are `GENERATED ALWAYS`, so no one can wedge a forged
row between two links; appends take a transaction-scoped advisory lock, so
concurrent writers cannot fork the chain; the actor's username is copied rather
than joined, so deleting a user does not erase their history.

### What the chain does not prove

**It proves consistency, not authenticity.** An attacker who can write to the
database can recompute the chain from the row they edited forward, and
verification will pass. Closing that gap needs an anchor outside the database:

- **Mirror** (`NEXUS_AUDIT_MIRROR_PATH`) — every entry is also appended as JSON
  to a file that can live on a different filesystem, be owned by a different
  user, or sit on append-only storage. The attacker must then tamper in two
  places. **Off by default**; the verify endpoint reports
  `external_anchor: NOT_CONFIGURED` when it is, rather than implying protection
  that is not there.
- **Checkpoints** — exporting the newest entry hash somewhere the database
  cannot reach makes any rewrite of earlier history detectable.
  **NOT IMPLEMENTED.**

The mirror is best-effort by design: a failed mirror write is logged loudly but
does not roll back the action. A full disk on a secondary log must not stop an
operator responding to an incident.

---

## 7. Secrets

- Secrets in configuration are `SecretStr`; a `repr()` in a log, a traceback,
  or a debugger cannot leak them.
- Logging redacts in two layers: by field name (broad substring matching), and
  by pattern within free text — URL credentials, `Bearer` tokens, `key: value`
  secrets. The second layer exists because a credential inside a driver's error
  message is invisible to the first.
- Audit `details` blobs are sanitised on write, since the audit log is the table
  most likely to be exported and read casually.
- The unauthenticated health endpoints scrub component diagnostics before
  returning them.
- `.env` is git-ignored; `.env.example` contains no values.
- Production refuses to start with the development signing key.

---

## 8. Network safety

`NEXUS_MONITORED_NETWORKS` is the platform's safety boundary: the CIDR blocks
the operator asserts they own.

- It defaults to **empty**, and empty means *nothing* is monitored — so an
  operator who has not declared their network cannot act on it.
- `0.0.0.0/0` is rejected at startup.
- An unparseable address is not monitored. Fails closed twice over.

Quarantine (**NOT IMPLEMENTED**) will verify device identity, confirm the
target is inside a declared network, require authorization and a reason, record
the attempt and its result, and provide restoration. It will act only through
infrastructure the operator has explicitly configured, and never against the
target device itself.

---

## 9. Reporting a vulnerability

This is a personal self-hosted project. If you find a security issue, open a
GitHub issue describing it, or contact the repository owner directly for
anything you would rather not post publicly.

## 10. Hardening checklist for deployment

- [ ] `NEXUS_ENVIRONMENT=production`
- [ ] `NEXUS_SECRET_KEY` set to 48+ random characters
- [ ] HTTPS terminated in front of NEXUS; `NEXUS_SESSION_COOKIE_SECURE=true`
- [ ] `NEXUS_CORS_ORIGINS` listing exact origins (never `*`)
- [ ] `NEXUS_TRUSTED_PROXY_HOPS` matching your actual proxy count (0 if none)
- [ ] `NEXUS_MONITORED_NETWORKS` listing only networks you own
- [ ] `NEXUS_AUDIT_MIRROR_PATH` on a separate filesystem
- [ ] Database user owning only the NEXUS database
- [ ] Backups tested by restoring them (see DEPLOYMENT.md — **NOT WRITTEN YET**)
- [ ] `python -m nexus.cli verify-audit` scheduled and alerting on failure
