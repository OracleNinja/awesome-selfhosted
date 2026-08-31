"""Image-generation providers.

The registry is explicit rather than discovered: knowing which services this
pipeline can reach should require reading one short list, not a plugin scan.
"""

from __future__ import annotations

from .base import (
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
from .mock import MockImageProvider

#: Name -> factory. ``mock`` is always available and is what CI uses.
#:
#: ``google-imagen`` is kept listed rather than deleted so that ``kdp providers``
#: can say *why* it cannot run. On the pinned SDK it is not a production option:
#: see its module docstring, and use ``google-gemini`` instead.
PROVIDERS = {
    "mock": MockImageProvider,
    "google-gemini": GoogleGeminiImageProvider,
    "google-imagen": GoogleImagenProvider,
    "higgsfield": HiggsfieldProvider,
}

#: Providers that cost money. Never the default, and `kdp generate` refuses one
#: without --confirm-spend.
METERED = ("google-gemini", "google-imagen", "higgsfield")


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
    "PROVIDERS",
    "GenerationRequest",
    "GenerationResult",
    "GoogleGeminiImageProvider",
    "GoogleImagenProvider",
    "HiggsfieldProvider",
    "ImageProvider",
    "MockImageProvider",
    "ProviderError",
    "ProviderUnavailable",
    "env_credential",
    "get_provider",
]
