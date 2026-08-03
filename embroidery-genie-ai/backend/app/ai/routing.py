"""Model routing.

Business logic asks for a capability tier. It never names a model. That
indirection is the whole point: swapping a provider, downgrading a task after
measuring it, or reacting to a price change is a config edit, not a code
change, and there is exactly one place to look when someone asks "what are we
actually calling?".

Tiers
-----
``LOW``     bounded extraction, classification, short structured output
``MEDIUM``  multi-step reasoning over modest context
``HIGH``    the strongest model, only where a weaker one demonstrably fails
``VISION``  a vision-capable model, used only when an image is genuinely needed

The model ids behind each tier come from settings (``AI_MODEL_<PROVIDER>_<TIER>``).
"""

from __future__ import annotations

from enum import Enum

from app.core.config import settings


class ModelTier(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    vision = "vision"


class Provider(str, Enum):
    anthropic = "anthropic"
    openai = "openai"


class NoProviderAvailable(RuntimeError):
    """No provider is both selected and credentialed."""


_ROUTES: dict[tuple[Provider, ModelTier], str] = {}


def _attribute(provider: Provider, tier: ModelTier) -> str:
    return f"ai_model_{provider.value}_{tier.value}"


def model_for(tier: ModelTier, provider: Provider) -> str:
    """The configured model id for a tier. Read live so tests can monkeypatch."""
    name = getattr(settings, _attribute(provider, tier), "")
    if not name:
        raise NoProviderAvailable(
            f"No model configured for {provider.value}/{tier.value}. "
            f"Set {_attribute(provider, tier).upper()}."
        )
    return name


def has_credentials(provider: Provider) -> bool:
    if provider is Provider.anthropic:
        return bool(settings.anthropic_api_key)
    return bool(settings.openai_api_key)


def resolve_provider() -> Provider:
    """Pick the provider for this call.

    ``auto`` prefers whichever key is present, Anthropic first when both are —
    a deliberate default, not an accident, because the vision tier is priced
    and measured against it in ``config/ai_pricing.json``.
    """
    choice = settings.ai_provider
    if choice == "none":
        raise NoProviderAvailable("AI_PROVIDER is 'none'.")
    if choice == "anthropic":
        if not has_credentials(Provider.anthropic):
            raise NoProviderAvailable("AI_PROVIDER is 'anthropic' but ANTHROPIC_API_KEY is unset.")
        return Provider.anthropic
    if choice == "openai":
        if not has_credentials(Provider.openai):
            raise NoProviderAvailable("AI_PROVIDER is 'openai' but OPENAI_API_KEY is unset.")
        return Provider.openai
    for provider in (Provider.anthropic, Provider.openai):
        if has_credentials(provider):
            return provider
    raise NoProviderAvailable("No AI provider credential is configured.")


def routing_table() -> list[dict]:
    """Every tier→model binding, for the dashboard and the cost report."""
    rows = []
    for provider in Provider:
        for tier in ModelTier:
            rows.append({
                "provider": provider.value,
                "tier": tier.value,
                "model": getattr(settings, _attribute(provider, tier), "") or None,
                "env_var": _attribute(provider, tier).upper(),
                "credentialed": has_credentials(provider),
            })
    return rows
