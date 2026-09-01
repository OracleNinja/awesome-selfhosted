"""Image-generation providers.

The registry is explicit rather than discovered: knowing which services this
pipeline can reach should require reading one short list, not a plugin scan.
"""

from __future__ import annotations

from dataclasses import dataclass

from .base import (
    Capability,
    CostClass,
    GenerationRequest,
    GenerationResult,
    ImageProvider,
    ProviderError,
    ProviderUnavailable,
    env_credential,
)
from .google_gemini import GoogleGeminiImageProvider
from .google_imagen import GoogleImagenProvider
from .higgsfield import HiggsfieldProvider
from .local_vector import LocalVectorProvider
from .mock import MockImageProvider

@dataclass(frozen=True, slots=True)
class Registration:
    """One provider, and everything the router needs to decide about it.

    The router reads this and the provider's own ``available()``. It contains no
    generation logic and no credential handling — those stay where they were.
    """

    #: Stable id. What ``--provider`` takes and what provenance records.
    id: str
    #: Builds the provider. Called only when the provider is actually wanted.
    factory: object
    #: Never inferred from whether a key happens to be present.
    cost: CostClass
    #: Lower runs first. Ties break on id, so selection is deterministic.
    priority: int
    #: One line for the providers table.
    note: str = ""


#: The registry. Adding a genuinely free provider means implementing
#: ``ImageProvider`` and adding a line here — the router needs no changes, and
#: has no per-provider logic to change.
#:
#: ``google-imagen`` is kept listed rather than deleted so that ``kdp providers``
#: can say *why* it cannot run. On the pinned SDK it is not a production option:
#: see its module docstring, and use ``google-gemini`` instead.
REGISTRY: tuple[Registration, ...] = (
    Registration(
        "local-vector", LocalVectorProvider, CostClass.FREE, 10,
        "the book's own SVG source, rasterised locally",
    ),
    Registration(
        "google-gemini", GoogleGeminiImageProvider, CostClass.METERED, 20,
        "generate_content at 4K; needs billing and explicit authorisation",
    ),
    Registration(
        "google-imagen", GoogleImagenProvider, CostClass.METERED, 30,
        "disabled on the pinned SDK, and 2K cannot reach 300 DPI",
    ),
    Registration(
        "higgsfield", HiggsfieldProvider, CostClass.UNKNOWN, 40,
        "no API or MCP contract was available, so its price is unestablished",
    ),
    Registration(
        "mock", MockImageProvider, CostClass.TEST, 99,
        "deterministic synthetic art; what CI runs, never a book",
    ),
)

#: Name -> factory. Derived, so the registry stays the single source of truth.
PROVIDERS = {r.id: r.factory for r in REGISTRY}

#: Providers that may cost money — METERED *and* UNKNOWN. Still a tuple of
#: names because that is what the CLI's spend guard has always compared against.
METERED = tuple(r.id for r in REGISTRY if r.cost.spends)


def registration(name: str) -> Registration:
    for entry in REGISTRY:
        if entry.id == name:
            return entry
    known = ", ".join(sorted(r.id for r in REGISTRY))
    raise ProviderError(f"unknown provider {name!r}; known providers: {known}")


def free_registrations() -> tuple[Registration, ...]:
    """Every provider the router may select, in the order it will try them."""
    return tuple(
        sorted(
            (r for r in REGISTRY if r.cost.routable),
            key=lambda r: (r.priority, r.id),
        )
    )


def get_provider(name: str) -> ImageProvider:
    try:
        return PROVIDERS[name]()
    except KeyError:
        known = ", ".join(sorted(PROVIDERS))
        raise ProviderError(
            f"unknown provider {name!r}; known providers: {known}"
        ) from None


__all__ = [
    "METERED",
    "REGISTRY",
    "Capability",
    "CostClass",
    "Registration",
    "free_registrations",
    "registration",
    "PROVIDERS",
    "GenerationRequest",
    "GenerationResult",
    "GoogleGeminiImageProvider",
    "GoogleImagenProvider",
    "HiggsfieldProvider",
    "LocalVectorProvider",
    "ImageProvider",
    "MockImageProvider",
    "ProviderError",
    "ProviderUnavailable",
    "env_credential",
    "get_provider",
]
