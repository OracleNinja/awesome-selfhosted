"""The provider router, tested adversarially.

The property that matters most is a negative one: this router cannot spend
money. Not "does not", under the conditions someone thought of — cannot, because
its candidate list is built from the free registrations and there is no branch
that reaches a metered provider. Several tests below try to make it spend
anyway, by making the free provider fail in every way a provider can fail, and
by handing it a metered provider that is available, credentialled and eager.

Providers here are stand-ins. Nothing touches a network, and the real registry
is used only where the test is about the real registry.
"""

from __future__ import annotations

import pytest

from kdp.generation import build_request, generate_book
from kdp.providers import METERED, REGISTRY, Registration, free_registrations
from kdp.providers.base import (
    Capability,
    CostClass,
    GenerationRequest,
    GenerationResult,
)
from kdp.providers.router import ProviderRouter
from kdp.providers.mock import _png
from fixtures import build_manifest


class Recorder:
    """A provider that records every call made to it."""

    def __init__(self, name, *, ok=True, reason="", retryable=True,
                 available=True, capability=None, raises=None):
        self.name, self.model = name, f"{name}-model"
        self._ok, self._reason, self._retryable = ok, reason, retryable
        self._available, self._capability, self._raises = available, capability, raises
        self.calls: list[str] = []

    def available(self):
        return self._available

    def unavailable_reason(self):
        return f"{self.name} is switched off for this test"

    def capability(self):
        return self._capability or Capability()

    def generate(self, request):
        self.calls.append(request.page_id)
        if self._raises:
            raise self._raises
        if self._ok:
            return GenerationResult(
                ok=True, page_id=request.page_id, data=_png(64, 80, b"x"),
                provider=self.name, model=self.model, width=64, height=80,
            )
        return GenerationResult(
            ok=False, page_id=request.page_id, provider=self.name,
            model=self.model, reason=self._reason or "no", retryable=self._retryable,
        )


def reg(provider, cost=CostClass.FREE, priority=10):
    return Registration(provider.name, lambda p=provider: p, cost, priority)


def _request(page_id="PAGE-004", **over):
    fields = dict(
        page_id=page_id, prompt_id="P", prompt_version="v1", prompt="draw",
        width=2520, height=3360, options={"aspect_ratio": "3:4"},
    )
    fields.update(over)
    return GenerationRequest(**fields)


# --- 1, 2, 3: free routing --------------------------------------------------


def test_a_free_provider_that_works_is_the_only_one_called():
    first, second = Recorder("first"), Recorder("second")
    router = ProviderRouter([reg(first, priority=10), reg(second, priority=20)])

    result = router.generate(_request())

    assert result.ok
    assert result.provider == "first"      # provenance names the real provider
    assert first.calls == ["PAGE-004"]
    assert second.calls == []


def test_a_failing_free_provider_falls_through_to_the_next_free_one():
    broken = Recorder("broken", ok=False, reason="renderer died")
    spare = Recorder("spare")
    router = ProviderRouter([reg(broken, priority=10), reg(spare, priority=20)])

    result = router.generate(_request())

    assert result.ok and result.provider == "spare"
    assert broken.calls and spare.calls


def test_exhausting_the_free_providers_stops_rather_than_escalating():
    a = Recorder("a", ok=False, reason="no source", retryable=False)
    b = Recorder("b", ok=False, reason="no source either", retryable=False)
    paid = Recorder("paid")
    router = ProviderRouter(
        [reg(a, priority=10), reg(b, priority=20), reg(paid, CostClass.METERED, 30)]
    )

    result = router.generate(_request())

    assert result.ok is False
    assert paid.calls == []
    assert "NO FREE PROVIDER CAN PRODUCE PAGE-004" in result.reason
    assert "Nothing was charged" in result.reason


# --- 4, 5, 10, 11: the paid boundary ---------------------------------------


@pytest.mark.parametrize("cost", [CostClass.METERED, CostClass.UNKNOWN])
def test_a_ready_paid_provider_is_never_called(cost):
    """Available, credentialled, eager — and still not called."""
    paid = Recorder("paid", available=True)
    router = ProviderRouter([reg(paid, cost, 10)])

    result = router.generate(_request())

    assert paid.calls == []
    assert result.ok is False
    assert result.retryable is False


def test_unknown_cost_is_treated_exactly_like_metered():
    assert CostClass.UNKNOWN.spends is True
    assert CostClass.UNKNOWN.routable is False
    assert "higgsfield" in METERED       # registered UNKNOWN, guarded as paid


def test_a_test_provider_is_never_auto_selected():
    """Worse than paid: mock art passes the pixel checks and would ship."""
    mock = Recorder("mock")
    router = ProviderRouter([reg(mock, CostClass.TEST, 99)])

    assert router.generate(_request()).ok is False
    assert mock.calls == []


def test_the_real_registry_offers_the_router_only_free_providers():
    assert [r.id for r in free_registrations()] == ["local-vector"]
    assert all(r.cost is CostClass.FREE for r in free_registrations())


def test_every_registered_provider_declares_a_cost():
    for entry in REGISTRY:
        assert isinstance(entry.cost, CostClass)


# --- capability filtering ---------------------------------------------------


def test_a_provider_that_cannot_reach_the_size_is_skipped_not_attempted():
    small = Recorder("small", capability=Capability(max_edge_px=2048))
    big = Recorder("big", capability=Capability(max_edge_px=8192))
    router = ProviderRouter([reg(small, priority=10), reg(big, priority=20)])

    result = router.generate(_request())

    assert small.calls == []
    assert result.provider == "big"


def test_the_skipped_provider_and_its_reason_are_reported():
    small = Recorder("small", capability=Capability(max_edge_px=2048))
    router = ProviderRouter([reg(small, priority=10)])

    reason = router.generate(_request()).reason
    assert "small" in reason and "tops out at 2048" in reason


def test_an_unsupported_aspect_ratio_is_a_skip_not_a_call():
    fussy = Recorder("fussy", capability=Capability(aspect_ratios=("1:1",)))
    router = ProviderRouter([reg(fussy)])

    assert fussy.calls == []
    assert "aspect ratio 3:4" in router.generate(_request()).reason


# --- failures stay failures -------------------------------------------------


def test_an_unavailable_provider_is_reported_with_its_own_reason():
    off = Recorder("off", available=False)
    router = ProviderRouter([reg(off)])

    result = router.generate(_request())
    assert off.calls == []
    assert "switched off for this test" in result.reason


def test_a_provider_that_raises_becomes_a_failure_not_an_exception():
    exploding = Recorder("boom", raises=RuntimeError("kaboom"))
    router = ProviderRouter([reg(exploding)])

    result = router.generate(_request())
    assert result.ok is False
    assert "kaboom" in result.reason


def test_a_transient_failure_leaves_the_page_retryable():
    flaky = Recorder("flaky", ok=False, reason="timed out", retryable=True)
    router = ProviderRouter([reg(flaky)])
    assert router.generate(_request()).retryable is True


def test_settled_failures_everywhere_make_the_page_settled():
    a = Recorder("a", ok=False, reason="no source", retryable=False)
    router = ProviderRouter([reg(a)])
    assert router.generate(_request()).retryable is False


def test_no_free_provider_at_all_is_reported_not_crashed():
    router = ProviderRouter([])
    result = router.generate(_request())
    assert result.ok is False
    assert router.available() is False
    assert "No free provider is registered" in router.unavailable_reason()


# --- 16: determinism --------------------------------------------------------

def test_equal_priorities_break_on_id_so_selection_is_deterministic():
    for _ in range(5):
        b, a = Recorder("bravo"), Recorder("alpha")
        router = ProviderRouter([reg(b, priority=10), reg(a, priority=10)])
        assert router.generate(_request()).provider == "alpha"


# --- 12, 17: the ledger and the missing page --------------------------------


def test_a_routed_page_records_the_provider_that_drew_it(tmp_path):
    book = build_manifest()
    router = ProviderRouter([reg(Recorder("first"), priority=10)])

    outcome = generate_book(book, router, tmp_path, now="2026-01-01T00:00:00+00:00")

    assert outcome.ok
    for record in outcome.manifest.assets:
        assert record.provider == "first"     # not "auto"


def test_a_page_no_free_provider_can_draw_stays_visibly_missing(tmp_path):
    book = build_manifest()
    dead = Recorder("dead", ok=False, reason="no source", retryable=False)
    router = ProviderRouter([reg(dead), reg(Recorder("paid"), CostClass.METERED, 20)])

    outcome = generate_book(book, router, tmp_path, now="2026-01-01T00:00:00+00:00")

    assert outcome.ok is False
    assert outcome.failed
    for record in outcome.manifest.assets:
        assert record.ships is False
        assert "NO FREE PROVIDER" in (record.failure_reason or "")


def test_a_settled_router_failure_is_not_retried_three_times(tmp_path):
    book = build_manifest()
    dead = Recorder("dead", ok=False, reason="no source", retryable=False)
    router = ProviderRouter([reg(dead)])

    generate_book(book, router, tmp_path, now="2026-01-01T00:00:00+00:00")

    # One router call per page, not three; the router asked its one provider once.
    assert len(dead.calls) == len(book.spec.art_pages)


# --- 9: a bad page is still a bad page --------------------------------------


def test_routing_does_not_touch_quality_judgement(tmp_path):
    """The router decides who draws. G9 decides whether it was any good."""
    from kdp.gates import asset_qa

    book = build_manifest()
    blank = Recorder("blank")
    blank.generate = lambda request: GenerationResult(   # a blank white page
        ok=True, page_id=request.page_id, data=_png(2480, 3280, b"x", defect="blank"),
        provider="blank", model="m", width=2480, height=3280,
    )
    router = ProviderRouter([reg(blank)])
    outcome = generate_book(book, router, tmp_path, now="2026-01-01T00:00:00+00:00")

    from kdp.generation import approve_asset

    manifest = outcome.manifest
    for record in manifest.assets:
        manifest = approve_asset(manifest, record.asset_id)

    result = asset_qa.run(manifest, assets_dir=tmp_path)
    assert result.status.value == "fail"


# --- 13: no credential anywhere ---------------------------------------------


def test_no_credential_reaches_a_router_failure_message(monkeypatch, tmp_path):
    for name in ("KDP_GOOGLE_API_KEY", "GOOGLE_API_KEY", "KDP_HIGGSFIELD_API_KEY"):
        monkeypatch.setenv(name, "sk-CANARY-NOT-REAL")

    router = ProviderRouter()          # the real registry, nothing available
    result = router.generate(_request())

    assert "CANARY" not in result.reason
    assert "CANARY" not in repr(result)
    assert "CANARY" not in router.unavailable_reason()
