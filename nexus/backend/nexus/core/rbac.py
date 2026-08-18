"""Role-based access control.

Authentication answers "who are you". Authorization answers "may you do this".
Conflating them produces the classic bug where every logged-in user can reach
an admin endpoint because the only check was "is there a session".

The model here is deliberately small and static:

    Role  ->  frozen set of Permissions

Permissions are verbs on resources (``devices:quarantine``), not screen names.
Endpoints declare the permission they need; they never test the role directly.
That indirection is what lets a fourth role be added later by editing one table
instead of auditing every route for ``if role == ADMIN``.

Why not per-object ACLs or a permissions table in the database?
--------------------------------------------------------------
Both were considered. A database-backed permission table lets an operator
invent roles at runtime, but it also means a corrupted or maliciously edited row
silently grants privileges, and it makes the effective policy invisible in code
review. For a single-tenant appliance with three roles, a matrix in source —
reviewable in a diff, testable without a database, impossible to change without
a deploy — is the safer trade. If custom roles are ever needed, the shape here
(role resolves to a permission set) is the same shape a database would store,
so the migration path is real.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    """Who a user is, in one word.

    Ordered from most to least privileged. ``str`` mixin so the value
    serialises directly to JSON and stores as a plain string column.
    """

    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"


class Permission(str, Enum):
    """A single authorised action.

    Naming is ``resource:verb``. Read permissions are separated from write
    permissions so a VIEWER can be given genuine visibility — the point of the
    role — without any ability to change state.
    """

    # Observation
    DASHBOARD_READ = "dashboard:read"
    EVENTS_READ = "events:read"
    DEVICES_READ = "devices:read"
    DETECTIONS_READ = "detections:read"
    RULES_READ = "rules:read"
    SENSORS_READ = "sensors:read"
    JOBS_READ = "jobs:read"
    SYSTEM_STATUS_READ = "system:status:read"

    # Investigation and response
    DETECTIONS_TRIAGE = "detections:triage"  # acknowledge, resolve, suppress
    DEVICES_ANNOTATE = "devices:annotate"  # label, tag, mark trusted
    JOBS_MANAGE = "jobs:manage"  # retry, cancel
    LAB_OPERATE = "lab:operate"  # run laboratory scenarios

    # Sensitive: changes what the platform detects or does to the network
    DEVICES_QUARANTINE = "devices:quarantine"
    RULES_WRITE = "rules:write"
    SENSORS_MANAGE = "sensors:manage"
    INTEGRATIONS_WRITE = "integrations:write"
    EVIDENCE_DELETE = "evidence:delete"
    LAB_MANAGE = "lab:manage"  # create/destroy laboratory environments

    # Administrative
    USERS_MANAGE = "users:manage"
    AUTH_SETTINGS_WRITE = "auth:settings:write"
    AUDIT_READ = "audit:read"
    RETENTION_MANAGE = "retention:manage"
    BACKUP_MANAGE = "backup:manage"


# Permissions every authenticated user holds. Read-only, never destructive.
_VIEWER_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.DASHBOARD_READ,
        Permission.EVENTS_READ,
        Permission.DEVICES_READ,
        Permission.DETECTIONS_READ,
        Permission.RULES_READ,
        Permission.SENSORS_READ,
        Permission.JOBS_READ,
        Permission.SYSTEM_STATUS_READ,
    }
)

# An operator investigates and responds, but cannot change who has access, edit
# detection logic, or delete evidence. Quarantine is granted because responding
# to an active threat is the operator's job; every use is audited and reversible.
_OPERATOR_PERMISSIONS: frozenset[Permission] = _VIEWER_PERMISSIONS | frozenset(
    {
        Permission.DETECTIONS_TRIAGE,
        Permission.DEVICES_ANNOTATE,
        Permission.DEVICES_QUARANTINE,
        Permission.JOBS_MANAGE,
        Permission.LAB_OPERATE,
    }
)

# Admin holds everything. Enumerated by union rather than "all permissions" so
# that adding a new sensitive permission is a conscious decision in this file
# rather than something an admin silently inherits.
_ADMIN_PERMISSIONS: frozenset[Permission] = _OPERATOR_PERMISSIONS | frozenset(
    {
        Permission.RULES_WRITE,
        Permission.SENSORS_MANAGE,
        Permission.INTEGRATIONS_WRITE,
        Permission.EVIDENCE_DELETE,
        Permission.LAB_MANAGE,
        Permission.USERS_MANAGE,
        Permission.AUTH_SETTINGS_WRITE,
        Permission.AUDIT_READ,
        Permission.RETENTION_MANAGE,
        Permission.BACKUP_MANAGE,
    }
)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: _ADMIN_PERMISSIONS,
    Role.OPERATOR: _OPERATOR_PERMISSIONS,
    Role.VIEWER: _VIEWER_PERMISSIONS,
}

# Actions serious enough that the UI must require a typed confirmation and the
# API records a mandatory reason. Kept next to the permission matrix so the two
# cannot drift apart.
SENSITIVE_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.DEVICES_QUARANTINE,
        Permission.RULES_WRITE,
        Permission.INTEGRATIONS_WRITE,
        Permission.EVIDENCE_DELETE,
        Permission.USERS_MANAGE,
        Permission.AUTH_SETTINGS_WRITE,
        Permission.SENSORS_MANAGE,
        Permission.RETENTION_MANAGE,
        Permission.LAB_MANAGE,
    }
)


def permissions_for(role: Role) -> frozenset[Permission]:
    """Effective permissions for a role.

    An unknown role resolves to the empty set rather than raising. If a
    database row somehow holds a role this build does not know about — a
    downgrade after a migration, say — the safe interpretation is "no
    privileges", not a crash and not a guess.
    """
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in permissions_for(role)


def requires_confirmation(permission: Permission) -> bool:
    return permission in SENSITIVE_PERMISSIONS
