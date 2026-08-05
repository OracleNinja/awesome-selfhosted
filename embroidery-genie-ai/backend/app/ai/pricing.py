"""Token → dollars, from external configuration only.

The rates are operator-supplied data loaded from a JSON file. They are never
compiled into the application and never influence a decision the product makes:
they turn recorded token counts into an estimate for the cost dashboard.

A model with no configured rate produces ``None``, not a guess. Every number
this module returns is labelled ``estimated`` and carries the rate and the
``as_of`` date it was computed from, so a figure in a report can always be
traced back to the assumption behind it.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.config import settings

log = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Rate:
    """Dollars per million tokens for one model."""

    model: str
    input: float
    output: float
    cache_write: float | None = None
    cache_read: float | None = None


@dataclass(frozen=True)
class PricingTable:
    rates: dict[str, Rate]
    currency: str
    as_of: str | None
    source: str
    confidence: str
    path: str | None
    loaded: bool

    def rate_for(self, model: str) -> Rate | None:
        return self.rates.get(model)

    def to_dict(self) -> dict:
        return {
            "configured": self.loaded and bool(self.rates),
            "currency": self.currency,
            "as_of": self.as_of,
            "source": self.source,
            "confidence": self.confidence,
            "file": self.path,
            "priced_models": sorted(self.rates),
            "disclaimer": (
                "Costs are estimates computed from operator-configured rates, not "
                "amounts billed by a provider. Models absent from the rate file "
                "record tokens with a null cost rather than a guessed one."
            ),
        }


_EMPTY = PricingTable(
    rates={},
    currency="USD",
    as_of=None,
    source="not configured",
    confidence="unknown",
    path=None,
    loaded=False,
)


def _resolve_path() -> Path:
    configured = settings.ai_pricing_file
    path = Path(configured)
    return path if path.is_absolute() else BACKEND_ROOT / path


@lru_cache(maxsize=8)
def _load(path_str: str, mtime: float) -> PricingTable:
    path = Path(path_str)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.info(
            "No AI pricing file at %s — AI usage will be recorded in tokens with "
            "no cost estimate.", path,
        )
        return _EMPTY
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("AI pricing file %s could not be read (%s); costs unpriced.", path, exc)
        return _EMPTY

    rates: dict[str, Rate] = {}
    for model, entry in (raw.get("models") or {}).items():
        if not isinstance(entry, dict):
            continue
        try:
            rates[model] = Rate(
                model=model,
                input=float(entry["input"]),
                output=float(entry["output"]),
                cache_write=_optional_float(entry.get("cache_write")),
                cache_read=_optional_float(entry.get("cache_read")),
            )
        except (KeyError, TypeError, ValueError):
            log.warning("Ignoring malformed pricing entry for %s in %s", model, path)

    return PricingTable(
        rates=rates,
        currency=str(raw.get("currency", "USD")),
        as_of=raw.get("as_of"),
        source=str(raw.get("source", "operator-configured estimate")),
        confidence=str(raw.get("confidence", "estimate")),
        path=str(path),
        loaded=True,
    )


def _optional_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def table() -> PricingTable:
    path = _resolve_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    return _load(str(path), mtime)


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> tuple[float | None, dict | None]:
    """Estimated dollars for one call, plus the rate it was derived from.

    Returns ``(None, None)`` for an unpriced model. Callers must persist the
    null rather than substituting zero — a zero would understate the real bill
    and quietly corrupt every average built on top of it.
    """
    rate = table().rate_for(model)
    if rate is None:
        return None, None

    # Cached input, where a provider reports it, is billed at its own rate.
    # Falling back to the plain input rate over-states rather than under-states.
    read_rate = rate.cache_read if rate.cache_read is not None else rate.input
    write_rate = rate.cache_write if rate.cache_write is not None else rate.input

    total = (
        input_tokens * rate.input
        + output_tokens * rate.output
        + cache_read_tokens * read_rate
        + cache_write_tokens * write_rate
    ) / 1_000_000.0

    basis = {
        "model": model,
        "input_per_mtok": rate.input,
        "output_per_mtok": rate.output,
        "as_of": table().as_of,
        "confidence": table().confidence,
    }
    return round(total, 8), basis


def reload() -> None:
    """Drop the cached table (tests, and after an operator edits the file)."""
    _load.cache_clear()
