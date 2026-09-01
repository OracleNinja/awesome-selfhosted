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
from enum import Enum
from typing import Protocol

from ..errors import KDPError


class ProviderError(KDPError):
    """A provider could not be used at all."""


class ProviderUnavailable(ProviderError):
    """The provider is not configured — no credentials, or not installed."""


class CostClass(str, Enum):
    """What calling a provider costs, as data rather than a hard-coded list.

    ``UNKNOWN`` is not a gap in the record — it is a verdict, and it is
    deliberately indistinguishable from ``METERED`` everywhere it matters. A
    provider whose price nobody has established is not free; assuming otherwise
    is how a bill arrives. Registering a provider without stating its cost gets
    you ``UNKNOWN``, which means the router will not call it.
    """

    #: Costs nothing to run. The only class the router will select on its own.
    FREE = "free"
    #: Costs nothing but produces synthetic art. Never auto-selected: a mock
    #: page passes the pixel checks and would ship, which is worse than a page
    #: that is visibly missing.
    TEST = "test"
    #: Costs money per call.
    METERED = "metered"
    #: Nobody has established what it costs. Treated as METERED.
    UNKNOWN = "unknown"

    @property
    def spends(self) -> bool:
        """True when calling this provider may cost money."""
        return self in (CostClass.METERED, CostClass.UNKNOWN)

    @property
    def routable(self) -> bool:
        """True when the router may select this provider without being asked."""
        return self is CostClass.FREE


@dataclass(frozen=True, slots=True)
class Capability:
    """What a provider can be asked for. Declared, not guessed.

    Kept deliberately small: the router needs to know whether a provider can
    produce *this page*, not everything about it.
    """

    #: Largest edge, in pixels, the provider can return. None means no limit.
    max_edge_px: int | None = None
    #: Aspect ratio labels the provider accepts. Empty means it does not care.
    aspect_ratios: tuple[str, ...] = ()
    #: Free-text note for the providers table.
    note: str = ""

    def refusal(self, request: "GenerationRequest") -> str | None:
        """Why this provider cannot serve ``request``, or None if it can."""
        longest = max(request.width, request.height)
        if self.max_edge_px is not None and longest > self.max_edge_px:
            return (
                f"needs {longest} px on the long edge; this provider tops out "
                f"at {self.max_edge_px}"
            )
        wanted = str((request.options or {}).get("aspect_ratio") or "")
        if self.aspect_ratios and wanted and wanted not in self.aspect_ratios:
            return (
                f"cannot produce aspect ratio {wanted}; it accepts "
                f"{', '.join(self.aspect_ratios)}"
            )
        return None


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    """One request for one page."""

    page_id: str
    prompt_id: str
    prompt_version: str
    #: The rendered prompt text, prohibitions included. What a provider with
    #: no notion of a negative prompt should send.
    prompt: str
    width: int
    height: int
    #: The prompt with the prohibitions removed, for providers that take a
    #: negative prompt separately. Listing what to avoid in the *positive*
    #: prompt is a well-known way to get a model to draw exactly that.
    #:
    #: Declared after width/height on purpose: these fields were added later,
    #: and inserting them earlier would silently rebind every positional call
    #: that already existed.
    prompt_positive: str = ""
    #: The prohibitions on their own.
    prompt_negative: str = ""
    #: Provider-specific knobs. Never credentials.
    options: dict = field(default_factory=dict)
    #: Attempt number for this page, carried through into provenance.
    attempt: int = 1

    def __post_init__(self) -> None:
        if not self.prompt_positive:
            object.__setattr__(self, "prompt_positive", self.prompt)


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
    #: Whether trying this page again could plausibly succeed. False for the
    #: failures that are settled: a rejected key, billing not enabled, a quota
    #: of zero, a retired model, a malformed request. The runner stops on those
    #: instead of spending its attempt budget, because three attempts a page
    #: across forty pages is a hundred and twenty requests against a wall — and
    #: on a metered provider, a hundred and twenty chances to be billed for
    #: nothing. Declared last so no positional call is rebound.
    retryable: bool = True

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

    # Optional, and read with getattr rather than required, so that adding it
    # did not break the providers that already existed:
    #
    #   capability() -> Capability      what this provider can be asked for
    #   readiness()  -> dict[str, bool] each precondition, separately
    #   unavailable_reason() -> str     why available() is False


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
