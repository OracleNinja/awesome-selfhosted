"""Password hashing and policy.

Why hashing, and why this hash
------------------------------
A password database will eventually be read by someone who should not have it —
a backup on the wrong disk, a SQL injection, a stolen laptop. Storing the
password itself means that moment is total. Storing a hash means the attacker
must guess.

The hash therefore has to be *slow* and *memory-hungry*, which is the exact
opposite of what you want from SHA-256. A GPU computes billions of SHA-256
hashes per second; the same GPU computes Argon2id far more slowly because each
guess must allocate and randomly access a large block of memory, and memory
bandwidth is the one thing you cannot buy cheaply in parallel.

Argon2id specifically: it is the winner of the Password Hashing Competition and
the OWASP first choice. The ``id`` variant combines Argon2i's resistance to
side-channel attacks on the memory access pattern with Argon2d's resistance to
GPU cracking.

Three properties the library gives us for free, each of which people get wrong
when hand-rolling:

* **A per-password salt**, generated automatically and stored inside the hash
  string. Without it, identical passwords produce identical hashes and one
  precomputed table breaks every account at once.
* **Encoded parameters.** The stored string carries the cost parameters used,
  so raising them later does not invalidate existing hashes — old passwords
  still verify, and can be transparently upgraded on next login.
* **Constant-time comparison**, so an attacker cannot learn how much of a hash
  they got right by measuring how long the comparison took.

Why the work happens in a thread
--------------------------------
Hashing deliberately costs tens of milliseconds of CPU. On an async server, CPU
work in a coroutine blocks the *entire* event loop: every other in-flight
request — every dashboard poll, every live event stream — stops until it
finishes. So verification and hashing run in a worker thread via
``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import secrets
import string
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import (
    HashingError,
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

from nexus.core.config import Settings
from nexus.core.logging import get_logger

logger = get_logger(__name__)

# Minimums enforced on every password. Length dominates: a 12-character
# passphrase has far more entropy than an 8-character password with a symbol in
# it, and composition rules mostly teach people to write "Password1!".
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024

# Passwords longer than this are rejected before hashing. Argon2's cost scales
# with input size, so an unbounded password field is a denial-of-service vector:
# a 10 MB "password" would tie up a worker thread for a long time.


@dataclass(frozen=True)
class PasswordPolicyError(Exception):
    """A proposed password does not meet policy. Message is safe to show."""

    message: str

    def __str__(self) -> str:
        return self.message


class PasswordHashingUnavailable(RuntimeError):
    """The hashing backend failed (out of memory, for example).

    Raised rather than falling back to a weaker algorithm. A system that
    silently degrades its password hashing under memory pressure is a system
    that stores weak hashes exactly when it is under attack.
    """


class PasswordService:
    """Hashes and verifies passwords using parameters from configuration.

    Constructed with settings rather than reading globals, so the test suite
    can use cheap parameters (tests would otherwise spend minutes hashing) while
    production uses strong ones.
    """

    def __init__(self, settings: Settings) -> None:
        self._hasher = PasswordHasher(
            time_cost=settings.password_hash_time_cost,
            memory_cost=settings.password_hash_memory_kib,
            parallelism=settings.password_hash_parallelism,
        )
        # A hash of a random value, used to burn the same CPU time when the
        # username does not exist — see `verify_dummy`.
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(32))

    # ------------------------------------------------------------- hashing --

    async def hash(self, password: str) -> str:
        """Hash a password. Raises :class:`PasswordPolicyError` if it is weak."""
        validate_password(password)
        try:
            return await asyncio.to_thread(self._hasher.hash, password)
        except HashingError as exc:  # pragma: no cover - resource exhaustion
            logger.error("password_hashing_failed", extra={"error": type(exc).__name__})
            raise PasswordHashingUnavailable(
                "Password hashing is temporarily unavailable."
            ) from exc

    async def verify(self, password_hash: str, password: str) -> bool:
        """Check a password against a stored hash. Never raises on mismatch."""
        try:
            await asyncio.to_thread(self._hasher.verify, password_hash, password)
        except VerifyMismatchError:
            return False
        except (VerificationError, InvalidHashError):
            # A corrupt or foreign hash string. Treated as a failed login, and
            # logged, because it means a row was written by something other
            # than this code path.
            logger.warning("password_hash_unverifiable")
            return False
        return True

    async def verify_dummy(self) -> None:
        """Burn equivalent CPU when no such user exists.

        Without this, "unknown username" returns in microseconds while "wrong
        password" takes ~50ms, and anyone can enumerate valid accounts with a
        stopwatch. The login endpoint calls this on the not-found path so both
        outcomes cost the same.
        """
        try:
            await asyncio.to_thread(self._hasher.verify, self._dummy_hash, "not-the-password")
        except VerificationError:
            return

    def needs_rehash(self, password_hash: str) -> bool:
        """True when a stored hash used weaker parameters than current policy.

        Cost parameters must rise as hardware gets faster. Because the encoded
        hash carries the parameters it was made with, an account can be upgraded
        silently at the one moment the plaintext is legitimately available: a
        successful login.
        """
        try:
            return bool(self._hasher.check_needs_rehash(password_hash))
        except InvalidHashError:  # pragma: no cover - defensive
            return True


def validate_password(password: str) -> None:
    """Enforce the password policy.

    Length-first, with a small blocklist of the passwords that appear in every
    breach corpus. A full compromised-password check (Have I Been Pwned's
    k-anonymity API) is deliberately *not* done here: it requires an outbound
    network call from an air-gapped-capable security appliance, and a login
    path that depends on a third party is a login path that fails when that
    third party does.
    """
    if not isinstance(password, str) or not password:
        raise PasswordPolicyError("A password is required.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
    if password.lower() in _COMMON_PASSWORDS:
        raise PasswordPolicyError("That password appears in public breach lists. Choose another.")
    if len(set(password)) < 5:
        raise PasswordPolicyError("Password must contain at least 5 distinct characters.")


# Not a substitute for a real breach corpus — just the handful that survive a
# 12-character minimum and would otherwise be someone's first choice.
_COMMON_PASSWORDS = frozenset(
    {
        "password1234",
        "passwordpassword",
        "administrator",
        "123456789012",
        "qwertyuiop123",
        "letmeinletmein",
        "welcome123456",
        "changemenow12",
        "nexuspassword",
    }
)


def generate_password(length: int = 20) -> str:
    """Generate a strong password for bootstrapping the first admin account.

    Uses ``secrets`` (the OS CSPRNG), never ``random`` — ``random`` is a
    Mersenne Twister whose entire future output can be predicted from 624
    observed outputs, which is fine for simulations and catastrophic for
    credentials.

    Ambiguous characters are excluded because this password gets read off a
    terminal and typed into a browser once.
    """
    alphabet = (
        string.ascii_lowercase.replace("l", "").replace("o", "")
        + string.ascii_uppercase.replace("I", "").replace("O", "")
        + string.digits.replace("0", "").replace("1", "")
        + "!@#$%^&*-_=+"
    )
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        try:
            validate_password(candidate)
        except PasswordPolicyError:  # pragma: no cover - astronomically rare
            continue
        return candidate
