"""Supabase authentication.

Supabase issues signed JWTs.  Two verification paths are supported:

* **HS256** — the legacy shared-secret scheme. Verify with ``SUPABASE_JWT_SECRET``.
* **RS256/ES256** — the newer asymmetric scheme. Fetch and cache the project's
  JWKS and verify against the published public key.

Both check ``aud`` and expiry.  Nothing in the app trusts a header, a query
parameter, or a client-supplied user id — the subject always comes out of a
verified token.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx
import jwt
from jwt import PyJWKClient

from .config import settings

log = logging.getLogger(__name__)

_JWKS_CACHE: dict[str, tuple[float, PyJWKClient]] = {}
_JWKS_TTL = 3600.0


class AuthError(Exception):
    """Raised when a token cannot be verified."""


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    email: str
    role: str = "authenticated"
    claims: dict | None = None

    @property
    def is_service(self) -> bool:
        return self.role == "service_role"


def _jwks_client() -> PyJWKClient:
    url = f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    cached = _JWKS_CACHE.get(url)
    now = time.time()
    if cached and now - cached[0] < _JWKS_TTL:
        return cached[1]
    client = PyJWKClient(url, cache_keys=True)
    _JWKS_CACHE[url] = (now, client)
    return client


def decode_token(token: str) -> dict:
    """Verify a Supabase JWT and return its claims."""
    if not token:
        raise AuthError("Missing bearer token.")

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise AuthError(f"Malformed token: {exc}") from exc

    algorithm = header.get("alg", "HS256")
    options = {"verify_aud": True, "require": ["exp", "sub"]}

    try:
        if algorithm.startswith("HS"):
            if not settings.supabase_jwt_secret:
                raise AuthError(
                    "SUPABASE_JWT_SECRET is not configured; cannot verify HS256 tokens."
                )
            return jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
                options=options,
            )
        if not settings.supabase_url:
            raise AuthError("SUPABASE_URL is not configured; cannot fetch signing keys.")
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=[algorithm],
            audience="authenticated",
            options=options,
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Session expired. Sign in again.") from exc
    except jwt.InvalidAudienceError as exc:
        raise AuthError("Token was not issued for this application.") from exc
    except jwt.PyJWTError as exc:
        raise AuthError(f"Invalid token: {exc}") from exc
    except httpx.HTTPError as exc:  # pragma: no cover - network path
        raise AuthError(f"Could not reach the auth provider: {exc}") from exc


def user_from_token(token: str) -> AuthenticatedUser:
    claims = decode_token(token)
    subject = claims.get("sub")
    if not subject:
        raise AuthError("Token has no subject.")
    email = claims.get("email") or (claims.get("user_metadata") or {}).get("email") or ""
    return AuthenticatedUser(
        id=str(subject),
        email=str(email),
        role=str(claims.get("role", "authenticated")),
        claims=claims,
    )


def dev_user() -> AuthenticatedUser:
    """Deterministic local user, only reachable when ALLOW_DEV_AUTH is on."""
    return AuthenticatedUser(
        id="00000000-0000-4000-8000-000000000001",
        email=settings.dev_user_email,
        role="authenticated",
        claims={"dev": True},
    )
