"""Session and CSRF token primitives.

Two different secrets with two different jobs:

* A **session token** proves "this browser already logged in". It is stored in
  an ``HttpOnly`` cookie so JavaScript cannot read it, which means an XSS bug
  cannot exfiltrate the session.
* A **CSRF token** proves "this request was made by our own page, not by
  another site". It must therefore be readable by our JavaScript — the whole
  point is that our page can send it and another origin cannot read it.

Why the database stores hashes, not tokens
------------------------------------------
Same reasoning as passwords: a database dump must not hand over live sessions.
The cookie carries a random string; the ``user_sessions`` row stores its
SHA-256.

But unlike a password, a fast hash is correct here. Argon2 exists to make
*guessing* expensive, and guessing only matters when the input has low entropy —
humans pick "summer2024". A session token is 256 bits from the OS CSPRNG; there
is nothing to guess, and making every request pay 50ms of Argon2 to look up a
session would be a self-inflicted denial of service.

Lookup by hash is also what keeps validation to a single indexed query: hash the
cookie, probe the unique index. No table scan, no per-row comparison.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# 32 bytes = 256 bits, URL-safe base64 encoded (~43 characters). Comfortably
# beyond brute force: an attacker guessing 10^12 tokens per second would still
# need longer than the age of the universe.
TOKEN_BYTES = 32


def generate_token() -> str:
    """Generate a token from the operating system's CSPRNG.

    ``secrets`` reads from the OS entropy source. ``random`` must never be used
    here: it is a Mersenne Twister, and observing 624 of its outputs reveals its
    entire internal state and therefore every token it will ever produce.
    """
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """SHA-256 of a token, hex encoded, for storage and lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_compare(left: str, right: str) -> bool:
    """Compare two strings without leaking their common prefix through timing.

    A naive ``==`` returns as soon as it finds a differing byte. Measure enough
    requests and that timing difference reveals the secret one byte at a time —
    a practical attack over a LAN, which is exactly where NEXUS runs.
    ``hmac.compare_digest`` always examines the whole input.
    """
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
