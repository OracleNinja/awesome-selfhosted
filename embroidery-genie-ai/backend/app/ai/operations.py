"""The operation registry.

Every AI call in the product is declared here before it can be made. An
operation carries its own budget: how much input it may send, how much output
it may receive, how long it may take, how many times it may be retried and what
happens when it fails. There is no path to a provider that skips this table —
``app/ai/gateway.py`` refuses an unregistered operation name.

Adding a new AI feature means adding a row here, which means someone has to
answer "what does this cost and what happens when it breaks?" at the moment the
feature is written rather than after the first invoice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.ai.routing import ModelTier
from app.core.config import settings

FailureMode = Literal["deterministic_fallback", "error"]


@dataclass(frozen=True)
class Operation:
    name: str
    tier: ModelTier
    purpose: str
    #: Bumped whenever the prompt changes. Part of the cache key, so a prompt
    #: edit invalidates old answers instead of silently serving them.
    prompt_version: str
    max_input_tokens: int
    max_output_tokens: int
    timeout_seconds: int
    max_attempts: int
    cacheable: bool
    on_failure: FailureMode
    #: The env flag that switches this one operation off.
    enable_flag: str
    #: True when the operation cannot be done by code. Documented per the
    #: audit: anything a deterministic implementation can do must not be here.
    requires_model: bool = True

    def enabled(self) -> bool:
        return bool(getattr(settings, self.enable_flag, False))

    def to_dict(self) -> dict:
        return {
            "operation": self.name,
            "tier": self.tier.value,
            "purpose": self.purpose,
            "prompt_version": self.prompt_version,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "timeout_seconds": self.timeout_seconds,
            "max_attempts": self.max_attempts,
            "cacheable": self.cacheable,
            "on_failure": self.on_failure,
            "enable_flag": self.enable_flag.upper(),
            "enabled": self.enabled(),
        }


VISION_ANALYSIS = "vision_analysis"


def _beta(strict: int, relaxed: int) -> int:
    return strict if settings.ai_beta_cost_mode else relaxed


def operation(name: str) -> Operation:
    """Look up an operation, rebuilding it from current settings.

    Budgets are read live rather than frozen at import so that beta cost mode
    and the env-tunable ceilings apply without a restart in tests.
    """
    if name != VISION_ANALYSIS:
        raise UnknownOperation(name)
    return Operation(
        name=VISION_ANALYSIS,
        tier=ModelTier.vision,
        purpose=(
            "Describe what the artwork depicts — subject, lettering, style — so "
            "the operator gets human-readable notes. Cannot move the "
            "compatibility score, the stitch count, the price or the export."
        ),
        prompt_version="2026-08-02.1",
        max_input_tokens=min(settings.ai_max_input_tokens, _beta(4000, 8000)),
        max_output_tokens=min(settings.ai_max_output_tokens, _beta(700, 1200)),
        timeout_seconds=settings.ai_timeout_seconds,
        max_attempts=max(1, min(settings.ai_max_attempts, _beta(2, 4))),
        cacheable=True,
        on_failure="deterministic_fallback",
        enable_flag="vision_analysis_enabled",
    )


class UnknownOperation(KeyError):
    def __init__(self, name: str):
        super().__init__(
            f"'{name}' is not a registered AI operation. Declare it in "
            f"app/ai/operations.py with a budget before calling a model."
        )


ALL_OPERATIONS = (VISION_ANALYSIS,)


def inventory() -> list[dict]:
    return [operation(name).to_dict() for name in ALL_OPERATIONS]


#: Work that deliberately does **not** call a model. Kept next to the registry
#: so the separation is a checked artefact rather than a claim in a document.
#: Each entry names the module that does the job in code.
DETERMINISTIC_BY_DESIGN: dict[str, str] = {
    "compatibility_score": "app/services/analyzer.py::score_design",
    "colour_reduction": "app/embroidery/raster.py::quantize",
    "thread_matching": "app/embroidery/threads.py (CIE L*a*b* nearest match)",
    "stitch_generation": "app/embroidery/digitizer.py",
    "stitch_count": "app/embroidery/pattern.py::Pattern.stitch_count",
    "run_time_estimate": "app/embroidery/pattern.py",
    "thread_consumption": "app/embroidery/reports.py",
    "pricing_and_quotes": "app/services/pricing.py::calculate",
    "quantity_breaks": "app/services/pricing.py",
    "machine_compatibility": "app/services/machines.py",
    "file_validation": "app/embroidery/raster.py, app/embroidery/svg_parse.py",
    "format_conversion": "app/embroidery/writers/",
    "mockup_generation": "app/services/mockup.py",
    "voice_command_parsing": "app/services/voice.py (rule-based)",
}
