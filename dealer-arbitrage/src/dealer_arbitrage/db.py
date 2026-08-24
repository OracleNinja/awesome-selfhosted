"""SQLite access layer and the append-only audit trail.

Every mutation that could become externally visible goes through `audit()`.
The schema itself refuses to let audit rows be edited or deleted, and refuses to
mark outreach as sent or an application as submitted without an approval record.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from . import paths


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class DBError(RuntimeError):
    pass


def connect(db_path: Path | str | None = None, *, create: bool = True) -> sqlite3.Connection:
    path = Path(db_path) if db_path else paths.DB_PATH
    if not path.exists() and not create:
        raise DBError(f"database not found: {path} — run `da init` first")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)  # autocommit; explicit txns below
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: Path | str | None = None) -> Path:
    """Create or upgrade the database from schema.sql. Idempotent."""
    paths.ensure_dirs()
    path = Path(db_path) if db_path else paths.DB_PATH
    schema = paths.SCHEMA_PATH.read_text(encoding="utf-8")
    conn = connect(path)
    try:
        conn.executescript(schema)
        audit(conn, action="init_db", summary=f"schema applied to {path.name}",
              actor="system", external=False)
    finally:
        conn.close()
    return path


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


# --------------------------------------------------------------------- audit
def audit(conn: sqlite3.Connection, *, action: str, summary: str,
          entity_type: str | None = None, entity_id: int | None = None,
          actor: str = "agent", external: bool = False,
          detail: Mapping[str, Any] | None = None,
          approval_id: int | None = None) -> int:
    """Append one row to the audit log. Never raises on detail serialization."""
    try:
        detail_json = json.dumps(detail, default=str) if detail else None
    except (TypeError, ValueError):
        detail_json = json.dumps({"unserializable_detail": str(detail)})
    cur = conn.execute(
        "INSERT INTO audit_log (ts, actor, action, entity_type, entity_id, external,"
        " summary, detail, approval_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (utcnow(), actor, action, entity_type, entity_id, int(bool(external)),
         summary, detail_json, approval_id),
    )
    return int(cur.lastrowid)


# ----------------------------------------------------------------- utilities
def insert(conn: sqlite3.Connection, table: str, values: Mapping[str, Any]) -> int:
    cols = [c for c in values if values[c] is not None or True]
    placeholders = ",".join("?" for _ in cols)
    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    cur = conn.execute(sql, [_encode(values[c]) for c in cols])
    return int(cur.lastrowid)


def update(conn: sqlite3.Connection, table: str, row_id: int,
           values: Mapping[str, Any]) -> None:
    if not values:
        return
    sets = ",".join(f"{c}=?" for c in values)
    conn.execute(f"UPDATE {table} SET {sets} WHERE id=?",
                 [_encode(v) for v in values.values()] + [row_id])


def upsert_company(conn: sqlite3.Connection, values: Mapping[str, Any]) -> int:
    """Insert a company or merge into the existing one, keyed on normalized_name.

    Merge rule: a non-null incoming value fills a null stored value. It never
    silently overwrites an existing non-null value — a contradiction between two
    sources is a research question for a human, not something to paper over.
    """
    name = (values.get("name") or "").strip()
    if not name:
        raise DBError("company requires a name")
    norm = normalize_name(name)
    existing = conn.execute(
        "SELECT * FROM companies WHERE normalized_name = ?", (norm,)).fetchone()
    if existing is None:
        payload = dict(values)
        payload["normalized_name"] = norm
        return insert(conn, "companies", payload)

    conflicts: dict[str, tuple[Any, Any]] = {}
    fill: dict[str, Any] = {}
    for key, new in values.items():
        if key in ("id", "normalized_name") or new is None:
            continue
        old = existing[key] if key in existing.keys() else None
        if old in (None, "", "unknown"):
            fill[key] = new
        elif _encode(old) != _encode(new):
            conflicts[key] = (old, new)
    if fill:
        update(conn, "companies", int(existing["id"]), fill)
    if conflicts:
        audit(conn, action="company_merge_conflict",
              summary=f"conflicting values for {name}; kept stored values",
              entity_type="company", entity_id=int(existing["id"]),
              detail={k: {"stored": v[0], "incoming": v[1]} for k, v in conflicts.items()})
    return int(existing["id"])


def normalize_name(name: str) -> str:
    lowered = name.lower().strip()
    for token in (" inc.", " inc", " llc", " l.l.c.", " ltd", " ltd.", " co.",
                  " corporation", " corp.", " corp", ",", "  "):
        lowered = lowered.replace(token, " ")
    return " ".join(lowered.split())


def _encode(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str)
    if isinstance(value, bool):
        return int(value)
    return value


def rows(conn: sqlite3.Connection, sql: str,
         params: Sequence[Any] = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def one(conn: sqlite3.Connection, sql: str,
        params: Sequence[Any] = ()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def scalar(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = (),
           default: Any = 0) -> Any:
    row = conn.execute(sql, params).fetchone()
    if row is None or row[0] is None:
        return default
    return row[0]


def field(row: Any, key: str, default: Any = None) -> Any:
    """Read a key from a dict OR a sqlite3.Row (which has keys() but no .get())."""
    if row is None:
        return default
    if isinstance(row, Mapping):
        return row.get(key, default)
    try:
        return row[key] if key in row.keys() else default
    except (IndexError, KeyError, TypeError, AttributeError):
        return default


def loads(value: Any, default: Any = None) -> Any:
    """Decode a JSON column that may be NULL or already-decoded."""
    if value in (None, ""):
        return default if default is not None else None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default if default is not None else value


def add_task(conn: sqlite3.Connection, *, title: str, detail: str | None = None,
             kind: str = "human", priority: int = 3,
             target_id: int | None = None, blocking_reason: str | None = None,
             entity_type: str | None = None, entity_id: int | None = None,
             due_at: str | None = None) -> int:
    """Raise a task. De-duplicates on (title, target_id) among open tasks."""
    existing = conn.execute(
        "SELECT id FROM tasks WHERE title = ? AND IFNULL(target_id,-1) = IFNULL(?,-1)"
        " AND status IN ('open','in_progress')", (title, target_id)).fetchone()
    if existing:
        return int(existing["id"])
    task_id = insert(conn, "tasks", {
        "title": title, "detail": detail, "kind": kind, "priority": priority,
        "target_id": target_id, "blocking_reason": blocking_reason,
        "entity_type": entity_type, "entity_id": entity_id, "due_at": due_at,
    })
    audit(conn, action="task_created", summary=title, entity_type="task",
          entity_id=task_id, detail={"kind": kind, "reason": blocking_reason})
    return task_id
