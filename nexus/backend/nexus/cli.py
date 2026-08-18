"""Operator command line.

    python -m nexus.cli create-admin --username ian
    python -m nexus.cli verify-audit
    python -m nexus.cli show-config

Why a CLI at all
----------------
The first administrator has to come from somewhere, and every wrong answer to
that question is a security hole:

* **A hardcoded default account** (``admin``/``admin``) is the single most
  exploited weakness in self-hosted software. Someone always forgets to change
  it, and scanners try it first.
* **A setup wizard on an open endpoint** means that between deployment and
  first login, anyone who reaches the port becomes the administrator.
* **A password in an environment variable** ends up in shell history, in
  ``docker inspect``, in a compose file committed to a repository, and in the
  process listing.

So the first account is created by someone with shell access to the host, which
is a privilege they already have, and the password is either typed
interactively or generated and printed once. It is never stored in plaintext,
never logged, and never passed as a command-line argument — arguments are
visible in ``ps`` to every user on the machine.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from collections.abc import Awaitable, Callable

from sqlalchemy import func, select

from nexus.core.config import ConfigurationError, Settings, load_settings
from nexus.core.logging import configure_logging
from nexus.core.passwords import PasswordPolicyError, PasswordService, validate_password
from nexus.core.rbac import Role
from nexus.db.models.user import User
from nexus.db.session import Database
from nexus.services.audit import AuditAction, AuditActor, AuditService
from nexus.services.users import UserService


async def _with_database(settings: Settings, work: Callable[..., Awaitable[int]]) -> int:
    """Open a database, run one command, always dispose."""
    database = Database(settings)
    if not await database.connect():
        print(
            "Cannot reach the database. Check NEXUS_DATABASE_URL and that PostgreSQL is running.",
            file=sys.stderr,
        )
        return 69  # EX_UNAVAILABLE
    try:
        async with database.session() as session:
            return await work(session, settings)
    finally:
        await database.dispose()


# ------------------------------------------------------------ create-admin --


async def _create_admin(session, settings: Settings, args: argparse.Namespace) -> int:
    passwords = PasswordService(settings)
    audit = AuditService(session, settings)
    from nexus.services.auth import AuthService

    auth = AuthService(session, settings, passwords, audit)
    users = UserService(session, passwords, audit, auth)

    existing_admins = (
        await session.execute(
            select(func.count())
            .select_from(User)
            .where(User.role == Role.ADMIN.value, User.is_active.is_(True))
        )
    ).scalar_one()

    if existing_admins and not args.force:
        print(
            f"This deployment already has {existing_admins} active "
            "administrator(s). Use --force to add another.",
            file=sys.stderr,
        )
        return 1

    password: str | None = None
    if args.prompt_password:
        # getpass reads without echoing and without touching shell history.
        password = getpass.getpass("Password for the new administrator: ")
        confirmation = getpass.getpass("Repeat password: ")
        if password != confirmation:
            print("Passwords do not match.", file=sys.stderr)
            return 1
        try:
            validate_password(password)
        except PasswordPolicyError as exc:
            print(f"Password rejected: {exc}", file=sys.stderr)
            return 1

    created = await users.create_user(
        username=args.username,
        display_name=args.display_name or args.username,
        role=Role.ADMIN,
        email=args.email,
        password=password,
        # A password the operator typed themselves does not need changing; a
        # generated one does not either, since only they have seen it. The
        # flag exists for the case where an admin sets up an account for
        # somebody else.
        must_change_password=args.require_password_change,
        actor=AuditActor.system("cli"),
    )
    await audit.record(
        AuditAction.ADMIN_BOOTSTRAPPED,
        actor=AuditActor.system("cli"),
        target_type="user",
        target_id=str(created.user.id),
        target_label=created.user.username,
        reason="Initial administrator created from the command line.",
    )

    print(f"\nCreated administrator: {created.user.username}")
    if created.generated_password:
        print("\n  Generated password (shown once, not recoverable):\n")
        print(f"      {created.generated_password}\n")
        print("  Store it in a password manager now, then sign in.")
    print()
    return 0


# ------------------------------------------------------------ verify-audit --


async def _verify_audit(session, settings: Settings, args: argparse.Namespace) -> int:
    audit = AuditService(session, settings)
    result = await audit.verify_chain()

    anchor = "configured" if settings.audit_mirror_path else "NOT CONFIGURED"
    if result.ok:
        print(f"Audit chain OK. {result.entries_checked} entries verified.")
        print(f"External anchor: {anchor}.")
        if not settings.audit_mirror_path:
            print(
                "  Note: without an off-database mirror this proves internal\n"
                "  consistency only. Anyone with database write access could\n"
                "  have rewritten the entire chain. See SECURITY.md."
            )
        return 0

    print("AUDIT CHAIN VERIFICATION FAILED", file=sys.stderr)
    print(f"  First invalid entry: {result.first_invalid_id}", file=sys.stderr)
    print(f"  Entries verified before failure: {result.entries_checked}", file=sys.stderr)
    print(f"  Detail: {result.detail}", file=sys.stderr)
    return 2


# ------------------------------------------------------------- show-config --


def _show_config(settings: Settings) -> int:
    """Print the redacted configuration — the first question in any support call."""
    print("NEXUS configuration (secrets redacted)\n")
    for key, value in settings.redacted_summary.items():
        print(f"  {key:24} {value}")
    if not settings.monitored_networks:
        print(
            "\n  WARNING: NEXUS_MONITORED_NETWORKS is empty. No address is "
            "considered\n  locally owned, so every active network operation "
            "will be refused."
        )
    print()
    return 0


# -------------------------------------------------------------------- main --


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nexus", description="NEXUS operator commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    admin = subparsers.add_parser("create-admin", help="Create the first administrator account.")
    admin.add_argument("--username", required=True)
    admin.add_argument("--display-name", default=None)
    admin.add_argument("--email", default=None)
    admin.add_argument(
        "--prompt-password",
        action="store_true",
        help="Type a password instead of having one generated. Never pass a "
        "password as an argument: arguments are visible in `ps` to every user.",
    )
    admin.add_argument(
        "--require-password-change",
        action="store_true",
        help="Force a password change at first login (for accounts you create "
        "on someone else's behalf).",
    )
    admin.add_argument(
        "--force",
        action="store_true",
        help="Create another administrator even though one already exists.",
    )

    subparsers.add_parser("verify-audit", help="Verify the audit log hash chain.")
    subparsers.add_parser("show-config", help="Print the redacted configuration.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        settings = load_settings()
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 78  # EX_CONFIG

    configure_logging(settings)

    if args.command == "show-config":
        return _show_config(settings)

    if args.command == "create-admin":
        return asyncio.run(
            _with_database(settings, lambda session, s: _create_admin(session, s, args))
        )

    if args.command == "verify-audit":
        return asyncio.run(
            _with_database(settings, lambda session, s: _verify_audit(session, s, args))
        )

    return 1  # pragma: no cover - argparse rejects unknown commands


if __name__ == "__main__":
    raise SystemExit(main())
