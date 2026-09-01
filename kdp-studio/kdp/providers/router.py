"""Chooses among the free providers, and stops at the paid boundary.

The router is a thin orchestration layer: it decides *which* provider is asked
for a page and records why. It contains no generation logic, no credential
handling, no prompt building, no quality judgement and no pricing. Every one of
those already exists somewhere else and stays there.

**It cannot spend money, by construction rather than by rule.** There is no
branch in this file that calls a metered provider — the candidate list is built
from :func:`free_registrations`, which filters on
:attr:`CostClass.routable`, and only ``FREE`` is routable. Authorising a spend
is done by naming the provider on the command line with ``--confirm-spend``,
which goes through the ordinary single-provider path and never comes here. That
is deliberate: a router that *could* call a paid provider under some condition
would need that condition to be correct forever, and this one does not have the
option.

``TEST`` providers are excluded too, and for a sharper reason than cost. The
mock draws clean synthetic line art that passes the pixel checks. A router that
fell back to it would fill a book with placeholder shapes that QA would wave
through — which is worse than a page that is visibly missing, because nothing
downstream would ever catch it.

**A failure is never a success.** Unavailable, unauthenticated, out of quota,
wrong size, refused, crashed — every one of them returns ``ok=False`` with a
reason. The page stays missing, G3 sees an unfulfilled page and G9 sees no
asset, exactly as they would if no provider had been tried at all.
"""

from __future__ import annotations

from .base import (
    Capability,
    CostClass,
    GenerationRequest,
    GenerationResult,
    ImageProvider,
)


def _capability(provider: object) -> Capability:
    """A provider's declared capability, or an unconstrained one."""
    declared = getattr(provider, "capability", None)
    if callable(declared):
        try:
            value = declared()
        except Exception:  # noqa: BLE001
            return Capability()
        if isinstance(value, Capability):
            return value
    return Capability()


def _reason(provider: object) -> str:
    ask = getattr(provider, "unavailable_reason", None)
    if callable(ask):
        try:
            return str(ask())
        except Exception:  # noqa: BLE001
            pass
    return "not available"


class Decision:
    """What the router did, in enough detail to put in a failure reason."""

    def __init__(self) -> None:
        self.considered: list[tuple[str, str]] = []

    def note(self, provider_id: str, outcome: str) -> None:
        self.considered.append((provider_id, outcome))

    def render(self) -> str:
        return "; ".join(f"{name} — {outcome}" for name, outcome in self.considered)


class ProviderRouter:
    """An :class:`ImageProvider` that delegates to the best free one available.

    Constructed with the registry by default; tests pass their own so that
    routing can be exercised without depending on what happens to be installed.
    """

    name = "auto"
    model = ""

    def __init__(self, registrations=None, blocked=None) -> None:
        from . import REGISTRY, free_registrations

        self._free = tuple(
            sorted(
                (r for r in registrations if r.cost.routable),
                key=lambda r: (r.priority, r.id),
            )
        ) if registrations is not None else free_registrations()
        #: Named only so the paid-boundary message can list what *would* be able
        #: to continue. They are never constructed and never called.
        self._blocked = tuple(
            sorted(
                (r for r in (registrations if registrations is not None else REGISTRY)
                 if not r.cost.routable and r.cost is not CostClass.TEST),
                key=lambda r: (r.priority, r.id),
            )
        ) if blocked is None else tuple(blocked)
        self._cache: dict[str, ImageProvider] = {}

    # -- availability -------------------------------------------------------

    def _provider(self, registration) -> ImageProvider:
        if registration.id not in self._cache:
            self._cache[registration.id] = registration.factory()
        return self._cache[registration.id]

    def readiness(self) -> dict[str, bool]:
        return {
            f"{r.id}_available": self._provider(r).available() for r in self._free
        } or {"any_free_provider_registered": False}

    def available(self) -> bool:
        return any(self._provider(r).available() for r in self._free)

    def unavailable_reason(self) -> str:
        if not self._free:
            return (
                "No free provider is registered. Nothing can be generated "
                "without authorising a metered provider explicitly."
            )
        lines = "; ".join(
            f"{r.id}: {_reason(self._provider(r))}"
            for r in self._free
            if not self._provider(r).available()
        )
        return f"No free provider is ready. {lines}"

    # -- the paid boundary --------------------------------------------------

    def _paid_boundary(self, request: GenerationRequest, decision: Decision) -> str:
        """The message that stands where a spend would otherwise happen."""
        could = ", ".join(f"{r.id} ({r.cost.value})" for r in self._blocked)
        return (
            f"NO FREE PROVIDER CAN PRODUCE {request.page_id}. "
            f"Considered: {decision.render() or 'none registered'}. "
            + (
                f"Providers that could potentially continue, each requiring "
                f"explicit authorisation: {could}. "
                if could
                else ""
            )
            + "Nothing was charged and no paid provider was called. The page "
            "remains missing."
        )

    # -- generation ---------------------------------------------------------

    def generate(self, request: GenerationRequest) -> GenerationResult:
        decision = Decision()
        retryable_seen = False

        for entry in self._free:
            provider = self._provider(entry)

            refusal = _capability(provider).refusal(request)
            if refusal is not None:
                decision.note(entry.id, f"cannot serve this page: {refusal}")
                continue

            if not provider.available():
                decision.note(entry.id, f"unavailable: {_reason(provider)}")
                continue

            try:
                result = provider.generate(request)
            except Exception as exc:  # noqa: BLE001
                # A provider that raises is a provider that failed. It is never
                # allowed to become an exception the runner has to interpret.
                decision.note(entry.id, f"raised {type(exc).__name__}: {exc}")
                retryable_seen = True
                continue

            if result.ok:
                # Returned as it came back, so provenance records the provider
                # that actually drew the page rather than the router.
                return result

            decision.note(entry.id, f"failed: {result.reason}")
            retryable_seen = retryable_seen or result.retryable

        return GenerationResult(
            ok=False,
            page_id=request.page_id,
            provider=self.name,
            model=self.model,
            reason=self._paid_boundary(request, decision),
            # Retryable only if something transient was seen. Exhausting the
            # free providers on settled failures is itself settled, and the
            # runner should stop rather than spend its budget on it.
            retryable=retryable_seen,
        )
