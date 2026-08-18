# API

Base path: `/api/v1`

The authoritative, always-current specification is the OpenAPI document the
application generates from its own schemas:

- Interactive: <http://127.0.0.1:8000/api/docs>
- Raw: <http://127.0.0.1:8000/api/openapi.json>

This file explains the conventions that apply everywhere — the parts a
generated document cannot tell you.

---

## Versioning

`/api/v1` is a contract.

- **Additive changes ship inside v1**: a new endpoint, a new optional request
  field, a new response field.
- **Breaking changes wait for v2**: removing a field, changing its type,
  narrowing an enum, tightening validation on an existing field.

Clients must ignore unknown response fields, or an additive change breaks them.

---

## Authentication

1. `POST /api/v1/auth/login` with `{"username": "...", "password": "..."}`.
2. The response sets an `HttpOnly` session cookie and returns a `csrf_token`.
3. Send the cookie on every request (browsers do this automatically) and the
   `X-CSRF-Token` header on every state-changing request.

```bash
curl -c jar -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"ian","password":"..."}'

curl -b jar -X POST http://127.0.0.1:8000/api/v1/users \
  -H "X-CSRF-Token: <token from login>" \
  -H 'Content-Type: application/json' \
  -d '{"username":"sam","display_name":"Sam","role":"VIEWER"}'
```

The CSRF token belongs in memory, not in `localStorage`: anything readable by
JavaScript is readable by injected JavaScript.

There is no API-key or token authentication yet. When it arrives it will be a
separate credential type with its own scopes, not a way to bypass CSRF on the
session path.

---

## Authorization

Each endpoint requires a permission, listed in its OpenAPI description. Roles
resolve to permission sets in `nexus/core/rbac.py`.

| | VIEWER | OPERATOR | ADMIN |
|---|---|---|---|
| Read dashboards, events, devices, detections | ✅ | ✅ | ✅ |
| Triage detections, annotate devices, quarantine | ❌ | ✅ | ✅ |
| Edit rules, sensors, integrations; delete evidence | ❌ | ❌ | ✅ |
| Manage users, read the audit log, change retention | ❌ | ❌ | ✅ |

`GET /api/v1/auth/me` returns the caller's effective permissions so a UI can
hide controls the user cannot use. **That list is for presentation only** — the
server checks every request regardless of what the client believes.

---

## Errors

Every failure — validation, authorization, dependency outage, unexpected crash
— returns the same shape:

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "The request payload failed validation.",
    "details": {"fields": [{"field": "body.username", "message": "..."}]},
    "request_id": "3c177905da884219951846d3173edfd5"
  }
}
```

Branch on `code`. The `message` is for humans and its wording may change.

| Code | Status | Meaning |
|---|---|---|
| `BAD_REQUEST` | 400 | Malformed request |
| `UNAUTHENTICATED` | 401 | No valid session, or credentials rejected |
| `PERMISSION_DENIED` | 403 | Authenticated, but your role lacks the permission |
| `CSRF_FAILED` | 403 | Missing or invalid `X-CSRF-Token` |
| `OPERATION_NOT_PERMITTED` | 403 | Refused by a safety rule — no role can override it |
| `NOT_FOUND` | 404 | No such resource (also returned for resources you may not see) |
| `METHOD_NOT_ALLOWED` | 405 | Wrong verb for this path |
| `CONFLICT` | 409 | Contradicts current state (duplicate name, stale version) |
| `PAYLOAD_TOO_LARGE` | 413 | Body exceeds the configured limit |
| `VALIDATION_FAILED` | 422 | Failed schema or domain validation |
| `RATE_LIMITED` | 429 | Too many requests; see the `Retry-After` header |
| `INTERNAL_ERROR` | 500 | Unexpected failure; quote `request_id` when reporting |
| `NOT_CONFIGURED` | 503 | The capability exists but this deployment has not set it up |
| `DEPENDENCY_UNAVAILABLE` | 503 | A dependency (database, sensor, upstream) is failing |

`NOT_CONFIGURED` and `DEPENDENCY_UNAVAILABLE` share a status code but are
different situations, and the remedy differs: configure it, versus fix it. A
client must never render either as "no results".

Internal exceptions are never returned. A 500 carries a correlation id and
nothing else; the stack trace is in the server log under the same id.

---

## Correlation

Every response carries `X-Request-ID`, and the same id appears in every log line
produced while handling that request and in any audit entry it wrote. Supply
your own by sending the header — it is honoured only if it is 8–64 characters
of `[A-Za-z0-9_-]`, since echoing arbitrary input into logs is log injection.

---

## Pagination

List endpoints that can grow without bound use **keyset** (cursor) pagination,
not `offset`:

```
GET /api/v1/audit?limit=50
GET /api/v1/audit?limit=50&before_id=1234
```

`OFFSET 100000` makes PostgreSQL walk and discard 100,000 rows, and on a table
that is receiving inserts the page boundaries shift under the reader, so rows
are seen twice or missed entirely. A cursor is one index seek and is stable
while new rows arrive.

---

## Rate limits

`POST /api/v1/auth/login` is throttled per source address and username. A
throttled response is `429` with `Retry-After` in seconds.

This limiter is per process and in memory — see SECURITY.md §2 for why that is
a speed bump rather than the control, and what the real control is.

---

## Health

| Endpoint | Question | Behaviour |
|---|---|---|
| `GET /api/v1/health/live` | Is the process up? | Always 200 if it can answer. Touches no dependency. |
| `GET /api/v1/health/ready` | Should it get traffic? | 200 when dependencies are usable, 503 otherwise. |
| `GET /api/v1/health` | What is wrong? | Always 200, with per-component status. |

All three are unauthenticated (probes run without credentials) and therefore
expose status only — no versions of dependencies, no connection details, and
diagnostics are scrubbed before they leave the process.

Point a liveness probe at `/health/live` and a readiness probe at
`/health/ready`. Pointing liveness at a dependency-checking endpoint turns a
database outage into a restart storm.

---

## Endpoints

Current as of M2. Descriptions, schemas, and per-endpoint error lists are in
the OpenAPI document.

### Health
| Method | Path | Permission |
|---|---|---|
| GET | `/health/live` | none |
| GET | `/health/ready` | none |
| GET | `/health` | none |

### Authentication
| Method | Path | Permission |
|---|---|---|
| POST | `/auth/login` | none |
| POST | `/auth/logout` | authenticated |
| GET | `/auth/me` | authenticated |
| POST | `/auth/password` | authenticated (own account) |
| GET | `/auth/sessions` | authenticated (own sessions) |
| DELETE | `/auth/sessions/{session_id}` | authenticated (own sessions) |

### Users
| Method | Path | Permission |
|---|---|---|
| GET | `/users` | `users:manage` |
| POST | `/users` | `users:manage` |
| GET | `/users/{user_id}` | `users:manage` |
| PATCH | `/users/{user_id}` | `users:manage` |
| PUT | `/users/{user_id}/role` | `users:manage` |
| PUT | `/users/{user_id}/active` | `users:manage` |
| POST | `/users/{user_id}/password-reset` | `users:manage` |
| POST | `/users/{user_id}/unlock` | `users:manage` |

Endpoints that change a user's role or status require a written `reason`, which
is stored in the audit log. "What happened" without "why" answers half the
question you will have six months later.

### Audit
| Method | Path | Permission |
|---|---|---|
| GET | `/audit` | `audit:read` |
| POST | `/audit/verify` | `audit:read` |

`POST /audit/verify` returns `external_anchor: CONFIGURED | NOT_CONFIGURED`.
A passing verification with `NOT_CONFIGURED` proves internal consistency only —
see SECURITY.md §6.

---

## Not yet implemented

No endpoints exist yet for events, devices, detections, sensors, jobs, the
laboratory, or quarantine. They are absent from the OpenAPI document too: this
project does not publish an endpoint before it works.
