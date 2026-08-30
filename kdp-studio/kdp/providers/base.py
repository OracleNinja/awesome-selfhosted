"""The image-generation provider boundary.

One interface, several backends, and nothing above this line knows which is in
use. That is what keeps Higgsfield a choice rather than a dependency.

**Credentials never appear here.** A provider reads its key from the
environment at call time and never records it: not in the request, not in the
result, not in provenance. ``provider`` and ``model`` name a service; they are
not secrets, and everything that *is* a secret stays out of the manifest.

**Failure is explicit.** A provider that cannot produce an image returns a
:class:`GenerationResult` with ``ok=False`` and a reason. It never returns a
placeholder, and the caller never substitutes one — a missing page must stay
missing so that QA can see it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

from ..errors import KDPError


class ProviderError(KDPError):
    """A provider could not be used at all."""


class ProviderUnavailable(ProviderError):
    """The provider is not configured — no credentials, or not installed."""


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    """One request for one page."""

    page_id: str
    prompt_id: str
    prompt_version: str
    #: The rendered prompt text.
    prompt: str
    width: int
    height: int
    #: Provider-specific knobs. Never credentials.
    options: dict = field(default_factory=dict)
    #: Attempt number for this page, carried through into provenance.
    attempt: int = 1


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """What came back. ``ok=False`` is a first-class outcome, not an exception."""

    ok: bool
    page_id: str
    #: Image bytes on success, None on failure.
    data: bytes | None = None
    provider: str = ""
    model: str = ""
    width: int | None = None
    height: int | None = None
    file_format: str = "png"
    #: Machine-readable failure reason. Required when ok is False.
    reason: str | None = None
    #: Provider-side identifiers useful for support tickets. Never a secret.
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.ok and not self.reason:
            raise ProviderError(
                f"failed generation for {self.page_id!r} carries no reason; an "
                "unexplained failure cannot be told apart from a lost file"
            )
        if self.ok and not self.data:
            raise ProviderError(
                f"successful generation for {self.page_id!r} carries no data"
            )


class ImageProvider(Protocol):
    """What every backend implements."""

    name: str
    model: str

    def available(self) -> bool:
        """True when this provider could run right now."""
        ...

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Produce one image, or return a failure result."""
        ...


def env_credential(*names: str) -> str | None:
    """Read the first credential present in the environment.

    Centralised so that no provider is tempted to accept a key as an argument
    and have it end up serialised into a manifest or a log line.
    """
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None
