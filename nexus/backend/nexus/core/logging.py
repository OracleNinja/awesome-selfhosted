"""Structured logging with request correlation and secret redaction.

The problem
-----------
``logger.info(f"user {name} logged in from {ip}")`` produces a line a human can
read and a machine cannot. When something breaks at 02:00 you want to ask "show
me every event for request 7f3a… ordered by time", and free text cannot answer
that. So every log record here is a JSON object with stable field names.

Correlation
-----------
A request is handled by one coroutine but touches many modules. Threading a
"request id" parameter through every function signature would be invasive, so
we use :class:`contextvars.ContextVar`, which is the async-aware equivalent of
thread-local storage: each request's task gets its own copy, and any code it
calls — however deep — sees the right value. The middleware sets it once; every
log line in that request carries it automatically.

Redaction
---------
The rule "never log secrets" is not enforceable by discipline alone; someone
will eventually log a whole request body. So redaction happens in the formatter,
the one place every record passes through, and it is key-based: a field named
``password``, ``token``, ``authorization`` and friends is replaced regardless of
where it came from. That is defence in depth, not a licence to log secrets.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

from nexus.core.config import LogFormat, Settings

# --------------------------------------------------------------------------
# Correlation context
# --------------------------------------------------------------------------

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)
component_var: ContextVar[str | None] = ContextVar("component", default=None)


def new_request_id() -> str:
    """Generate a correlation id.

    UUID4 rather than a counter: NEXUS may run several worker processes, and a
    per-process counter would collide across them, which is exactly when you
    most need the ids to be unique.
    """
    return uuid.uuid4().hex


def bind_request_id(request_id: str | None = None) -> str:
    value = request_id or new_request_id()
    request_id_var.set(value)
    return value


def bind_user(user_id: str | None) -> None:
    user_id_var.set(user_id)


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------

REDACTED = "[redacted]"

# Credentials embedded in a URL: postgresql://user:password@host/db, and the
# same shape in an https:// webhook or proxy URL. Driver and library error
# messages quote these back at you, so the pattern has to be scrubbed from
# free-text strings, not only from fields we know the name of.
_URL_CREDENTIALS_RE = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)(?P<user>[^\s:/@]+):(?P<secret>[^\s/@]*)@"
)

# `Authorization: Bearer eyJhbGciOi…`. A bearer scheme is always followed by
# its credential, so the whitespace-separated form is safe to match here.
_BEARER_RE = re.compile(r"(?i)\bbearer\s+\S+")

# `token=abc123`, `api_key: xyz`. These require an explicit `:` or `=` so that
# ordinary prose — "password reset requested" — is not mangled into
# "password=[redacted] requested", which would hide information an operator
# needs while protecting nothing.
_INLINE_SECRET_RE = re.compile(r"(?i)\b(token|api[_-]?key|password|passwd|secret)\b\s*[:=]\s*\S+")


def scrub_text(value: str) -> str:
    """Remove credentials from a free-text string.

    Key-based redaction cannot help when the secret is embedded in a sentence —
    "could not connect to postgresql://nexus:hunter2@db/nexus" is one string
    under a key called ``error``. This is the second layer, applied to every
    string value that passes through the logger and to any text the API returns
    from a component's diagnostics.
    """
    scrubbed = _URL_CREDENTIALS_RE.sub(r"\g<scheme>\g<user>:" + REDACTED + "@", value)
    scrubbed = _BEARER_RE.sub(f"Bearer {REDACTED}", scrubbed)
    return _INLINE_SECRET_RE.sub(lambda match: f"{match.group(1)}={REDACTED}", scrubbed)


# Substring match, lowercased. Broad on purpose: a false positive costs one
# unreadable debug field, a false negative costs a credential in a log file
# that is then shipped to wherever logs are shipped.
SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "auth_header",
    "cookie",
    "session_id",
    "api_key",
    "apikey",
    "private_key",
    "credential",
    "signature",
    "otp",
    "hash",
)

MAX_REDACTION_DEPTH = 6


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Recursively replace sensitive values.

    Depth-limited because log payloads occasionally contain deeply nested or
    self-referential structures, and a logger that hangs or overflows the stack
    is a denial of service in the observability path — precisely the component
    that must survive when everything else is failing.
    """
    if _depth >= MAX_REDACTION_DEPTH:
        return "[truncated]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if _is_sensitive_key(key_str):
                result[key_str] = REDACTED
            else:
                result[key_str] = redact(item, _depth=_depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [redact(item, _depth=_depth + 1) for item in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)


# --------------------------------------------------------------------------
# Formatters
# --------------------------------------------------------------------------

# Attributes the stdlib puts on every record; anything else was added by the
# caller via ``extra=`` and belongs in the structured payload.
_RESERVED_RECORD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Render records as one JSON object per line (JSON Lines).

    One object per line is what every log shipper understands and what ``jq``
    can stream, and it survives interleaved writes from multiple processes far
    better than a multi-line format.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _iso_timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }

        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        user_id = user_id_var.get()
        if user_id:
            payload["user_id"] = user_id
        component = component_var.get()
        if component:
            payload["component"] = component

        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_ATTRS or key.startswith("_"):
                continue
            payload[key] = value

        if record.exc_info:
            # The traceback goes to the log, never to the HTTP response.
            payload["exception"] = self.formatException(record.exc_info)

        safe = redact(payload)
        try:
            return json.dumps(safe, default=_json_fallback, separators=(",", ":"))
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return json.dumps(
                {
                    "ts": payload["ts"],
                    "level": "ERROR",
                    "logger": record.name,
                    "event": "log_serialisation_failed",
                    "original_event": str(record.getMessage()),
                }
            )


class ConsoleFormatter(logging.Formatter):
    """Human-readable format for local development.

    Same data, different presentation. Developers reading a terminal need the
    message first; machines need consistent keys. Both read the same records.
    """

    def format(self, record: logging.LogRecord) -> str:
        request_id = request_id_var.get()
        prefix = f"{_iso_timestamp(record.created)} {record.levelname:<7} {record.name}"
        if request_id:
            prefix += f" [{request_id[:8]}]"
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_RECORD_ATTRS and not key.startswith("_")
        }
        line = f"{prefix} {record.getMessage()}"
        if extras:
            safe = redact(extras)
            rendered = " ".join(f"{key}={value!r}" for key, value in sorted(safe.items()))
            line += f" | {rendered}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def _iso_timestamp(epoch_seconds: float) -> str:
    """UTC RFC3339 timestamp with millisecond precision.

    Always UTC: logs from a machine whose timezone changes at a DST boundary
    are unsortable, and incident timelines depend on sortable timestamps.
    """
    base = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch_seconds))
    millis = int((epoch_seconds % 1) * 1000)
    return f"{base}.{millis:03d}Z"


def _json_fallback(value: Any) -> str:
    return str(value)


# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------

_configured = False


def configure_logging(settings: Settings, *, force: bool = False) -> None:
    """Install the root handler. Idempotent.

    Logging is configured once, at process start, before anything else can emit
    a record — otherwise early messages are lost or double-printed. ``force``
    exists for tests that need to reconfigure.
    """
    global _configured
    if _configured and not force:
        return

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter() if settings.log_format is LogFormat.JSON else ConsoleFormatter()
    )
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    # These libraries log one line per request/query at INFO, which drowns the
    # application's own events. We keep their warnings, which are the useful part.
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "asyncio", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger.

    Plain stdlib loggers: callers pass structured fields through ``extra=``,
    which the formatter merges into the JSON payload. Using the stdlib means
    third-party libraries' records flow through the same formatter and
    redaction as ours — a wrapper type would only cover our own call sites.
    """
    return logging.getLogger(name)
