"""Tests for the AI cost controls.

Every claim in docs/AI_COST_CONTROLS.md that can be checked by code is checked
here. The provider is always a stub — no test in this file makes a network call
or spends a cent — but the stub is exercised through the real gateway, so the
budget checks, the ledger writes, the cache, the retry policy and the kill
switches are the production ones.
"""

from __future__ import annotations

import io
import uuid

import pytest
from PIL import Image

from tests.conftest import TEST_DATABASE_URL, requires_db


# ------------------------------------------------------------------ fixtures
@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Make sure the tables exist for the tests here that talk to the database
    directly rather than through ``app_client`` (which builds its own schema)."""
    if not TEST_DATABASE_URL:
        return
    import app.models  # noqa: F401 - registers every table on Base.metadata
    from app.db.base import Base
    from app.db.session import engine

    Base.metadata.create_all(engine)   # idempotent



@pytest.fixture
def artwork() -> bytes:
    image = Image.new("RGB", (900, 700), (255, 255, 255))
    for box, color in (
        ((40, 40, 400, 300), (18, 87, 166)),
        ((450, 60, 820, 320), (196, 22, 28)),
        ((40, 400, 820, 500), (11, 11, 11)),
    ):
        image.paste(Image.new("RGB", (box[2] - box[0], box[3] - box[1]), color), box[:2])
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def db_session():
    """A real session against the test database, rolled back afterwards."""
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def org(db_session):
    """A throwaway workspace, deleted afterwards so ledger sums stay isolated."""
    from app.models import Organization

    organization = Organization(
        name="Cost Test Shop", slug=f"cost-test-{uuid.uuid4().hex[:8]}"
    )
    db_session.add(organization)
    db_session.commit()
    yield organization
    db_session.delete(organization)   # cascades to ledger rows and cache entries
    db_session.commit()


@pytest.fixture
def other_org(db_session):
    from app.models import Organization

    organization = Organization(
        name="Rival Shop", slug=f"rival-{uuid.uuid4().hex[:8]}"
    )
    db_session.add(organization)
    db_session.commit()
    yield organization
    db_session.delete(organization)
    db_session.commit()


@pytest.fixture
def ai_on(monkeypatch):
    """Turn the AI layer on with a fake credential. No network is reached."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "vision_analysis_enabled", True)
    monkeypatch.setattr(settings, "ai_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test-not-a-real-key")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "ai_cache_enabled", True)
    monkeypatch.setattr(settings, "ai_budget_override", False)
    return settings


class StubProvider:
    """Records every call the gateway makes and returns a canned answer."""

    def __init__(self, *, fail_with=None, fail_times=0, text=None, tokens=(1000, 120)):
        self.calls: list[dict] = []
        self.fail_with = fail_with
        self.fail_times = fail_times
        self.text = text if text is not None else '{"subject": "a shield logo", ' \
            '"artwork_type": "logo", "contains_text": false, "text_content": "", ' \
            '"smallest_text_words": [], "has_gradients": false, "has_fine_lines": false, ' \
            '"has_outline_stroke": true, "dominant_shapes": ["shield"], ' \
            '"style_notes": "flat", "embroidery_concerns": []}'
        self.tokens = tokens

    def __call__(self, **kwargs):
        from app.ai.providers import ProviderResponse

        self.calls.append(kwargs)
        if self.fail_with is not None and len(self.calls) <= self.fail_times:
            raise self.fail_with
        return ProviderResponse(
            text=self.text,
            input_tokens=self.tokens[0],
            output_tokens=self.tokens[1],
            measured=True,
            request_id=f"req_{len(self.calls)}",
        )


@pytest.fixture
def stub(monkeypatch):
    def install(**kwargs):
        from app.ai import gateway
        from app.ai.routing import Provider

        provider = StubProvider(**kwargs)
        monkeypatch.setitem(gateway.DISPATCH, Provider.anthropic, provider)
        return provider

    return install


@pytest.fixture
def quota_neutral():
    """Give back the plan quota this test consumes.

    Designs created through ``app_client`` land in the shared development
    workspace and count against its free-plan allowance. Deleting the design
    does not refund it — the usage meter is append-only by design — so the test
    removes the rows it added rather than leaving later tests short.
    """
    from sqlalchemy import delete, func, select

    from app.db.session import SessionLocal
    from app.models import UsageEvent

    with SessionLocal() as db:
        watermark = db.scalar(select(func.max(UsageEvent.occurred_at)))
    yield
    with SessionLocal() as db:
        statement = delete(UsageEvent)
        if watermark is not None:
            statement = statement.where(UsageEvent.occurred_at > watermark)
        db.execute(statement)
        db.commit()


def context_for(db_session, organization, user_id=None):
    from app.ai import AIContext

    return AIContext(db=db_session, organization_id=organization.id, user_id=user_id)


# =========================================================== 1. the inventory
def test_the_only_ai_call_site_is_the_gateway():
    """An SDK call anywhere outside app/ai/providers.py would bypass the meter,
    the budget and the cache. Grep is the enforcement."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "providers.py" and path.parent.name == "ai":
            continue
        text = path.read_text(encoding="utf-8")
        for needle in ("messages.create(", "chat.completions.create(", "anthropic.Anthropic("):
            if needle in text:
                offenders.append(f"{path.relative_to(root)} contains {needle}")
    assert not offenders, (
        "External model calls must go through app/ai/gateway.py:\n  "
        + "\n  ".join(offenders)
    )


def test_deterministic_work_never_reaches_a_model(artwork, monkeypatch):
    """Pricing, scoring, stitch counts and format conversion must not change
    when the AI layer is unavailable."""
    from app.ai import gateway
    from app.services.analyzer import analyze_design
    from app.services.pricing import PricingInputs, calculate

    def explode(*args, **kwargs):
        raise AssertionError("a deterministic path called a model")

    monkeypatch.setattr(gateway, "run_vision_analysis", explode)
    monkeypatch.setattr("app.services.analyzer.run_vision_analysis", explode)

    report = analyze_design(artwork, "image/png", use_ai=False)
    assert 0 <= report.compatibility_score <= 100
    assert report.colors

    quote = calculate(PricingInputs(stitch_count=8000, color_count=4, quantity=24))
    assert quote.unit_cost > 0


def test_every_operation_declares_a_budget():
    from app.ai.operations import inventory

    for op in inventory():
        assert op["max_input_tokens"] > 0
        assert op["max_output_tokens"] > 0
        assert op["timeout_seconds"] > 0
        assert op["max_attempts"] >= 1
        assert op["on_failure"] in ("deterministic_fallback", "error")


def test_an_unregistered_operation_cannot_be_called():
    from app.ai.operations import UnknownOperation, operation

    with pytest.raises(UnknownOperation):
        operation("summarize_the_customer_database")


# ============================================================== 2. no hard-coding
def test_no_model_id_is_hard_coded_in_business_logic():
    """Model names may appear in app/core/config.py (the config surface) and
    nowhere else in the application."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    pattern = re.compile(r"\bclaude-[a-z0-9.\-]+|\bgpt-[0-9][a-z0-9.\-]*")
    offenders = []
    for path in root.rglob("*.py"):
        if path.relative_to(root).as_posix() == "core/config.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    assert not offenders, "Model ids belong in configuration:\n  " + "\n  ".join(offenders)


def test_every_tier_resolves_to_a_configured_model():
    from app.ai.routing import ModelTier, Provider, model_for

    for provider in Provider:
        for tier in ModelTier:
            assert model_for(tier, provider)


def test_tier_routing_follows_configuration(monkeypatch):
    from app.ai.routing import ModelTier, Provider, model_for
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_model_anthropic_vision", "some-other-model")
    assert model_for(ModelTier.vision, Provider.anthropic) == "some-other-model"


# ================================================= 3. context minimisation
def test_artwork_is_downscaled_before_it_is_sent():
    from app.ai.payload import normalize_artwork
    from app.core.config import settings

    big = Image.new("RGB", (4000, 3000), (200, 40, 40))
    buffer = io.BytesIO()
    big.save(buffer, format="PNG")

    normalized = normalize_artwork(buffer.getvalue(), "image/png")
    assert max(normalized.width, normalized.height) == settings.ai_image_max_edge_px
    assert len(normalized.data) < len(buffer.getvalue())
    # (4000 x 3000) / 750 = 16,000 tokens; the downscale must cut that hard.
    assert normalized.estimated_image_tokens < 2000


def test_normalisation_strips_metadata():
    """EXIF can carry GPS coordinates and an author name. None of it belongs in
    a request to a third party."""
    from app.ai.payload import normalize_artwork

    source = Image.new("RGB", (300, 200), (10, 120, 200))
    buffer = io.BytesIO()
    exif = source.getexif()
    exif[271] = "SecretCameraMaker"
    source.save(buffer, format="JPEG", exif=exif)
    assert b"SecretCameraMaker" in buffer.getvalue()

    normalized = normalize_artwork(buffer.getvalue(), "image/jpeg")
    assert b"SecretCameraMaker" not in normalized.data


@requires_db
def test_only_the_prompt_and_the_image_are_sent(artwork, ai_on, stub, db_session, org):
    from app.ai import gateway

    provider = stub()
    gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))

    sent = provider.calls[0]
    assert set(sent) == {
        "api_key", "model", "prompt", "artwork", "max_output_tokens", "timeout_seconds"
    }
    assert "Cost Test Shop" not in sent["prompt"]
    assert str(org.id) not in sent["prompt"]


# ================================================== 4. token and output limits
@requires_db
def test_output_is_always_bounded(artwork, ai_on, stub, db_session, org):
    from app.ai import gateway
    from app.ai.operations import VISION_ANALYSIS, operation

    provider = stub()
    gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))
    assert provider.calls[0]["max_output_tokens"] == operation(VISION_ANALYSIS).max_output_tokens
    assert provider.calls[0]["max_output_tokens"] > 0


@requires_db
def test_a_timeout_is_always_set(artwork, ai_on, stub, db_session, org):
    from app.ai import gateway

    provider = stub()
    gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))
    assert provider.calls[0]["timeout_seconds"] > 0


@requires_db
def test_oversized_input_is_reduced_rather_than_sent(artwork, ai_on, stub, db_session, org,
                                                     monkeypatch):
    """A tight input budget must shrink the payload deterministically, not
    truncate it, and not send it anyway."""
    from app.ai import gateway
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_max_input_tokens", 700)
    provider = stub()
    outcome = gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))

    assert outcome.status == "ok"
    sent = provider.calls[0]["artwork"]
    assert sent.estimated_image_tokens <= 700


@requires_db
def test_input_that_cannot_be_reduced_is_rejected_without_calling(
    artwork, ai_on, stub, db_session, org, monkeypatch
):
    from app.ai import gateway
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_max_input_tokens", 50)
    provider = stub()
    outcome = gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))

    assert outcome.status == "skipped"
    assert outcome.reason == "input_too_large"
    assert provider.calls == []


# ============================================================ 5. artwork hashing
def test_the_hash_is_deterministic_and_content_addressed(artwork):
    from app.ai.payload import normalize_artwork

    first = normalize_artwork(artwork, "image/png")
    second = normalize_artwork(artwork, "image/png")
    assert first.artwork_hash == second.artwork_hash
    assert len(first.artwork_hash) == 64


def test_hashing_the_normalised_bytes_widens_what_counts_as_the_same_file():
    """The same pixels re-encoded differently — another compression level, an
    alpha channel, added metadata — must be one artwork, not four.

    Hashing the raw upload would treat every one of these as new and pay again.
    """
    from app.ai.payload import content_hash, normalize_artwork

    base = Image.new("RGB", (500, 400), (255, 255, 255))
    base.paste(Image.new("RGB", (250, 200), (18, 87, 166)), (0, 0))

    variants = []
    for save_kwargs, mode in (
        ({"format": "PNG", "compress_level": 1}, "RGB"),
        ({"format": "PNG", "compress_level": 9}, "RGB"),
        ({"format": "PNG", "optimize": True}, "RGBA"),
        ({"format": "BMP"}, "RGB"),
    ):
        buffer = io.BytesIO()
        base.convert(mode).save(buffer, **save_kwargs)
        variants.append(buffer.getvalue())

    assert len({content_hash(v) for v in variants}) == 4, "the raw bytes should differ"
    assert len({normalize_artwork(v).artwork_hash for v in variants}) == 1


def test_different_resolutions_of_one_logo_are_not_conflated():
    """An exact hash can only miss; a perceptual hash could collide two
    different logos and serve one customer a description of another's artwork.
    Missing costs one API call. Colliding costs trust."""
    from app.ai.payload import normalize_artwork

    def render(size):
        image = Image.new("RGB", size, (255, 255, 255))
        image.paste(Image.new("RGB", (size[0] // 2, size[1] // 2), (18, 87, 166)), (0, 0))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    assert (
        normalize_artwork(render((2048, 2048))).artwork_hash
        != normalize_artwork(render((1024, 1024))).artwork_hash
    )


def test_different_artwork_hashes_differently(artwork):
    from app.ai.payload import normalize_artwork

    other = Image.new("RGB", (900, 700), (0, 160, 90))
    buffer = io.BytesIO()
    other.save(buffer, format="PNG")

    assert (
        normalize_artwork(artwork, "image/png").artwork_hash
        != normalize_artwork(buffer.getvalue(), "image/png").artwork_hash
    )


# ================================================================== 6. caching
@requires_db
def test_a_second_analysis_of_the_same_artwork_is_free(
    artwork, ai_on, stub, db_session, org
):
    from app.ai import gateway

    provider = stub()
    first = gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))
    second = gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))

    assert first.status == "ok"
    assert second.status == "cached"
    assert second.result == first.result
    assert len(provider.calls) == 1, "the second analysis reached the provider"


@requires_db
def test_the_cache_does_not_cross_tenants(artwork, ai_on, stub, db_session, org, other_org):
    """The whole point. Two workspaces with the same artwork each pay once and
    neither can read the other's answer."""
    from app.ai import gateway

    provider = stub()
    gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))
    outcome = gateway.run_vision_analysis(
        artwork, "image/png", context_for(db_session, other_org)
    )

    assert outcome.status == "ok", "the rival workspace read a cached answer"
    assert len(provider.calls) == 2


@requires_db
def test_a_prompt_change_invalidates_the_cache(artwork, ai_on, stub, db_session, org,
                                               monkeypatch):
    from app.ai import gateway, operations

    provider = stub()
    gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))

    real = operations.operation

    def bumped(name):
        op = real(name)
        return type(op)(**{**op.__dict__, "prompt_version": "9999-99-99.9"})

    monkeypatch.setattr(gateway, "operation", bumped)
    outcome = gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))

    assert outcome.status == "ok"
    assert len(provider.calls) == 2


@requires_db
def test_a_model_change_invalidates_the_cache(artwork, ai_on, stub, db_session, org,
                                              monkeypatch):
    from app.ai import gateway
    from app.core.config import settings

    provider = stub()
    gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))
    monkeypatch.setattr(settings, "ai_model_anthropic_vision", "a-different-model")
    outcome = gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))

    assert outcome.status == "ok"
    assert len(provider.calls) == 2


@requires_db
def test_a_cache_hit_is_recorded_in_the_ledger(artwork, ai_on, stub, db_session, org):
    """An unrecorded cache hit would make the hit rate unmeasurable, which is
    the metric that proves the cache is worth having."""
    from app.ai import gateway, ledger
    from app.models import AICallEvent

    stub()
    gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))
    gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))

    rows = db_session.query(AICallEvent).filter(
        AICallEvent.organization_id == org.id
    ).all()
    outcomes = [row.outcome for row in rows]
    assert ledger.SUCCESS in outcomes
    assert ledger.CACHE_HIT in outcomes
    hit = next(row for row in rows if row.outcome == ledger.CACHE_HIT)
    assert hit.cache_hit is True
    assert hit.input_tokens == 0 and hit.output_tokens == 0


@requires_db
def test_duplicate_artwork_is_detected(artwork, ai_on, stub, db_session, org):
    from app.ai import cache, gateway
    from app.ai.payload import normalize_artwork

    stub()
    digest = normalize_artwork(artwork, "image/png").artwork_hash
    assert not cache.previously_analyzed(db_session, org.id, digest)

    gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))
    assert cache.previously_analyzed(db_session, org.id, digest)


@requires_db
def test_duplicate_detection_is_tenant_scoped(artwork, ai_on, stub, db_session, org,
                                              other_org):
    from app.ai import cache, gateway
    from app.ai.payload import normalize_artwork

    stub()
    gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))
    digest = normalize_artwork(artwork, "image/png").artwork_hash

    assert cache.previously_analyzed(db_session, org.id, digest)
    assert not cache.previously_analyzed(db_session, other_org.id, digest)


# =================================================================== 7. ledger
@requires_db
def test_a_successful_call_is_metered(artwork, ai_on, stub, db_session, org):
    from app.ai import gateway, ledger
    from app.models import AICallEvent

    stub(tokens=(1234, 210))
    user_id = None
    gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org, user_id))

    row = db_session.query(AICallEvent).filter(
        AICallEvent.organization_id == org.id, AICallEvent.outcome == ledger.SUCCESS
    ).one()
    assert row.operation == "vision_analysis"
    assert row.provider == "anthropic"
    assert row.model
    assert row.tier == "vision"
    assert row.input_tokens == 1234
    assert row.output_tokens == 210
    assert row.tokens_measured is True
    assert row.latency_ms is not None
    assert row.artwork_hash


@requires_db
def test_the_ledger_stores_no_prompt_or_artwork(artwork, ai_on, stub, db_session, org):
    import json

    from app.ai import gateway
    from app.models import AICallEvent

    stub()
    gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))

    for row in db_session.query(AICallEvent).filter(
        AICallEvent.organization_id == org.id
    ):
        blob = json.dumps(row.meta)
        assert "shield logo" not in blob, "model output leaked into the ledger"
        assert "embroidery digitizer" not in blob, "prompt text leaked into the ledger"


@requires_db
def test_cost_is_computed_from_the_configured_rate(artwork, ai_on, stub, db_session, org,
                                                   monkeypatch):
    from app.ai import gateway, ledger, pricing
    from app.core.config import settings
    from app.models import AICallEvent

    monkeypatch.setattr(settings, "ai_model_anthropic_vision", "claude-haiku-4-5")
    stub(tokens=(1_000_000, 1_000_000))
    gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))

    rate = pricing.table().rate_for("claude-haiku-4-5")
    assert rate is not None, "the shipped pricing file lost its haiku entry"

    row = db_session.query(AICallEvent).filter(
        AICallEvent.organization_id == org.id, AICallEvent.outcome == ledger.SUCCESS
    ).one()
    assert float(row.estimated_cost_usd) == pytest.approx(rate.input + rate.output, rel=1e-6)


@requires_db
def test_an_unpriced_model_records_null_cost_not_zero(artwork, ai_on, stub, db_session, org,
                                                      monkeypatch):
    """A guessed zero would understate the bill and corrupt every average built
    on top of it. Unknown must stay unknown."""
    from app.ai import gateway, ledger
    from app.core.config import settings
    from app.models import AICallEvent

    monkeypatch.setattr(settings, "ai_model_anthropic_vision", "model-with-no-configured-rate")
    stub(tokens=(5000, 500))
    gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))

    row = db_session.query(AICallEvent).filter(
        AICallEvent.organization_id == org.id, AICallEvent.outcome == ledger.SUCCESS
    ).one()
    assert row.estimated_cost_usd is None
    assert row.input_tokens == 5000


def test_pricing_never_invents_a_rate():
    from app.ai import pricing

    assert pricing.estimate_cost("a-model-nobody-configured", 1000, 1000) == (None, None)


def test_the_pricing_file_is_labelled_as_an_estimate():
    from app.ai import pricing

    described = pricing.table().to_dict()
    assert described["confidence"] == "estimate"
    assert "estimate" in described["disclaimer"].lower()
    assert described["as_of"]


# =================================================================== 8. budgets
@requires_db
def test_a_tenant_that_exhausts_its_monthly_budget_is_stopped(
    artwork, ai_on, stub, db_session, org, monkeypatch
):
    from app.ai import gateway
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_monthly_tokens_per_tenant", 1500)
    provider = stub(tokens=(1400, 200))   # 1600 > 1500 after one call

    first = gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))
    assert first.status == "ok"

    # Different artwork, so the cache cannot mask the budget stop.
    other = Image.new("RGB", (800, 600), (30, 200, 120))
    buffer = io.BytesIO()
    other.save(buffer, format="PNG")
    second = gateway.run_vision_analysis(
        buffer.getvalue(), "image/png", context_for(db_session, org)
    )

    assert second.status == "blocked"
    assert second.reason == "budget_exceeded_tenant_monthly_tokens"
    assert len(provider.calls) == 1, "a blocked call still reached the provider"


@requires_db
def test_a_budget_block_names_what_still_works(artwork, ai_on, stub, db_session, org,
                                               monkeypatch):
    from app.ai import gateway
    from app.core.config import settings

    stub()
    # A fresh tenant has no recorded spend, so its first call always proceeds —
    # the budget gates on what has happened, not on a projection. Spend once,
    # then the limit binds.
    gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))
    monkeypatch.setattr(settings, "ai_monthly_tokens_per_tenant", 1)

    other = Image.new("RGB", (700, 500), (200, 30, 90))
    buffer = io.BytesIO()
    other.save(buffer, format="PNG")
    outcome = gateway.run_vision_analysis(
        buffer.getvalue(), "image/png", context_for(db_session, org)
    )

    assert outcome.status == "blocked"
    message = outcome.message.lower()
    assert "digitizing" in message or "analysis" in message
    assert "export" in message or "pricing" in message


@requires_db
def test_a_fresh_tenant_overshoots_by_at_most_one_request(
    artwork, ai_on, stub, db_session, org, monkeypatch
):
    """Gating on recorded spend means the last call of a period can overshoot.
    That is deliberate — refusing on a projection would deny service on an
    estimate — and the overshoot is bounded by the per-request ceiling."""
    from app.ai import gateway
    from app.ai.operations import VISION_ANALYSIS, operation
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_monthly_tokens_per_tenant", 1)
    stub(tokens=(900, 100))
    first = gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))
    assert first.status == "ok"

    op = operation(VISION_ANALYSIS)
    assert first.output_tokens <= op.max_output_tokens
    assert first.input_tokens <= op.max_input_tokens


@requires_db
def test_one_tenants_spend_does_not_block_another(artwork, ai_on, stub, db_session, org,
                                                  other_org, monkeypatch):
    from app.ai import gateway
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_monthly_tokens_per_tenant", 1500)
    stub(tokens=(1400, 200))

    gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))
    outcome = gateway.run_vision_analysis(
        artwork, "image/png", context_for(db_session, other_org)
    )
    assert outcome.status == "ok", "a neighbour's spend blocked this workspace"


@requires_db
def test_the_global_budget_stops_everyone(artwork, ai_on, stub, db_session, org, other_org,
                                          monkeypatch):
    from app.ai import gateway
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_monthly_tokens_per_tenant", 0)   # no tenant limit
    monkeypatch.setattr(settings, "ai_daily_tokens_per_tenant", 0)
    monkeypatch.setattr(settings, "ai_daily_tokens_per_user", 0)
    monkeypatch.setattr(settings, "ai_monthly_cost_global_usd", 0)
    monkeypatch.setattr(settings, "ai_monthly_tokens_global", 1)
    stub()

    outcome = gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))
    # The global window includes rows from every other test in the run, so the
    # limit of 1 token is exceeded either way — that is the point.
    if outcome.status == "ok":
        outcome = gateway.run_vision_analysis(
            artwork, "image/png", context_for(db_session, other_org)
        )
    assert outcome.status == "blocked"
    assert outcome.reason == "budget_exceeded_global_monthly_tokens"


@requires_db
def test_an_administrator_can_override_a_hard_stop(artwork, ai_on, stub, db_session, org,
                                                   monkeypatch):
    from app.ai import gateway
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_monthly_tokens_per_tenant", 1)
    monkeypatch.setattr(settings, "ai_budget_override", True)
    stub()

    outcome = gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))
    assert outcome.status == "ok"


@requires_db
def test_alerts_fire_at_fifty_eighty_and_one_hundred_percent(db_session, org):
    from app.ai import budget

    scope = budget.Scope("tenant_monthly_tokens", "tokens", 0, 1000, "test")
    assert scope.alert_level is None
    assert budget.Scope("s", "tokens", 500, 1000, "t").alert_level == 0.5
    assert budget.Scope("s", "tokens", 810, 1000, "t").alert_level == 0.8
    assert budget.Scope("s", "tokens", 1000, 1000, "t").alert_level == 1.0
    assert budget.Scope("s", "tokens", 1000, 1000, "t").exceeded


def test_an_unset_limit_never_blocks():
    from app.ai import budget

    scope = budget.Scope("anything", "usd", 9_999_999, 0, "no limit configured")
    assert not scope.enforced
    assert not scope.exceeded
    assert scope.alert_level is None


@requires_db
def test_a_blocked_call_is_recorded_but_charges_nothing(artwork, ai_on, stub, db_session,
                                                        org, monkeypatch):
    from app.ai import gateway, ledger
    from app.core.config import settings
    from app.models import AICallEvent

    stub()
    gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))
    monkeypatch.setattr(settings, "ai_monthly_tokens_per_tenant", 1)

    other = Image.new("RGB", (640, 480), (90, 30, 200))
    buffer = io.BytesIO()
    other.save(buffer, format="PNG")
    gateway.run_vision_analysis(
        buffer.getvalue(), "image/png", context_for(db_session, org)
    )

    row = db_session.query(AICallEvent).filter(
        AICallEvent.organization_id == org.id, AICallEvent.outcome == ledger.BLOCKED
    ).one()
    assert row.input_tokens == 0
    assert row.output_tokens == 0
    assert row.reason.startswith("budget_exceeded_")


# =================================================================== 9. retries
@requires_db
def test_a_retryable_error_is_retried_and_every_attempt_is_billed(
    artwork, ai_on, stub, db_session, org, monkeypatch
):
    from app.ai import gateway, ledger
    from app.ai.providers import ProviderError
    from app.core.config import settings
    from app.models import AICallEvent

    monkeypatch.setattr(settings, "ai_retry_backoff_seconds", 0.0)
    monkeypatch.setattr(settings, "ai_max_attempts", 3)
    monkeypatch.setattr(settings, "ai_beta_cost_mode", False)
    provider = stub(
        fail_with=ProviderError("429", code="rate_limited", retryable=True), fail_times=1
    )

    outcome = gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))
    assert outcome.status == "ok"
    assert len(provider.calls) == 2

    rows = db_session.query(AICallEvent).filter(
        AICallEvent.organization_id == org.id,
        AICallEvent.outcome.in_((ledger.SUCCESS, ledger.FAILURE)),
    ).all()
    assert len(rows) == 2, "the failed attempt was not billed"
    assert {row.attempt for row in rows} == {1, 2}
    assert any(row.outcome == ledger.FAILURE and row.input_tokens > 0 for row in rows)


@requires_db
def test_a_non_retryable_error_is_not_retried(artwork, ai_on, stub, db_session, org,
                                              monkeypatch):
    """Auth failures, invalid requests and token-limit rejections cost money to
    reconfirm and cannot succeed."""
    from app.ai import gateway
    from app.ai.providers import ProviderError
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_retry_backoff_seconds", 0.0)
    for code in ("auth_failed", "invalid_request", "permission_denied", "model_not_found"):
        provider = stub(
            fail_with=ProviderError(code, code=code, retryable=False), fail_times=99
        )
        outcome = gateway.run_vision_analysis(
            artwork, "image/png", context_for(db_session, org)
        )
        assert outcome.status == "failed"
        assert outcome.reason == code
        assert len(provider.calls) == 1, f"{code} was retried"


@requires_db
def test_retries_stop_at_the_configured_ceiling(artwork, ai_on, stub, db_session, org,
                                                monkeypatch):
    from app.ai import gateway
    from app.ai.operations import VISION_ANALYSIS, operation
    from app.ai.providers import ProviderError
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_retry_backoff_seconds", 0.0)
    provider = stub(
        fail_with=ProviderError("boom", code="server_error", retryable=True), fail_times=99
    )
    outcome = gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))

    assert outcome.status == "failed"
    assert len(provider.calls) == operation(VISION_ANALYSIS).max_attempts


@requires_db
def test_the_reported_attempt_count_is_what_actually_ran(
    artwork, ai_on, stub, db_session, org, monkeypatch
):
    """Reporting the ceiling instead of the real count overstates spend in the
    API response while the ledger says something else. Caught live against a
    bad key: a non-retryable auth failure was reported as two attempts."""
    from app.ai import gateway, ledger
    from app.ai.providers import ProviderError
    from app.core.config import settings
    from app.models import AICallEvent

    monkeypatch.setattr(settings, "ai_retry_backoff_seconds", 0.0)
    stub(fail_with=ProviderError("401", code="auth_failed", retryable=False), fail_times=99)

    outcome = gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))
    assert outcome.status == "failed"
    assert outcome.attempts == 1

    rows = db_session.query(AICallEvent).filter(
        AICallEvent.organization_id == org.id,
        AICallEvent.outcome.in_((ledger.SUCCESS, ledger.FAILURE)),
    ).all()
    assert len(rows) == outcome.attempts, "the response and the ledger disagree"


def test_error_classification_marks_the_right_errors_retryable():
    from app.ai.providers import ProviderError

    retryable = ProviderError("x", code="rate_limited", retryable=True)
    permanent = ProviderError("x", code="invalid_request", retryable=False)
    assert retryable.retryable
    assert not permanent.retryable


def test_sdk_auto_retry_is_disabled():
    """The SDKs retry twice by default. Those retries are billed and would not
    appear in the ledger, so they are switched off and the gateway owns retry."""
    import inspect

    from app.ai import providers

    for fn in (providers.call_anthropic, providers.call_openai):
        assert "max_retries=0" in inspect.getsource(fn)


@requires_db
def test_an_unparseable_response_is_not_retried(artwork, ai_on, stub, db_session, org):
    from app.ai import gateway

    provider = stub(text="I'm afraid I can't describe that.")
    outcome = gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))

    assert outcome.status == "failed"
    assert outcome.reason == "unparseable_response"
    assert len(provider.calls) == 1


# =============================================================== 10. kill switch
@requires_db
def test_the_global_kill_switch_stops_every_call(artwork, ai_on, stub, db_session, org,
                                                 monkeypatch):
    from app.ai import gateway
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_enabled", False)
    provider = stub()
    outcome = gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))

    assert outcome.status == "skipped"
    assert outcome.reason == "ai_disabled"
    assert provider.calls == []


@requires_db
def test_the_per_operation_switch_stops_just_that_operation(
    artwork, ai_on, stub, db_session, org, monkeypatch
):
    from app.ai import gateway
    from app.core.config import settings

    monkeypatch.setattr(settings, "vision_analysis_enabled", False)
    provider = stub()
    outcome = gateway.run_vision_analysis(artwork, "image/png", context_for(db_session, org))

    assert outcome.status == "skipped"
    assert outcome.reason == "operation_disabled"
    assert provider.calls == []
    assert settings.ai_enabled is True, "the global switch was collateral damage"


def test_the_product_still_works_with_ai_disabled(artwork, monkeypatch):
    """Analysis, scoring, digitizing, pricing and export must all survive
    AI_ENABLED=false with no degradation in the numbers."""
    from app.core.config import settings
    from app.embroidery import DigitizeOptions, TraceOptions, digitize_shapes, trace_image, write
    from app.services.analyzer import analyze_design
    from app.services.pricing import PricingInputs, calculate

    monkeypatch.setattr(settings, "ai_enabled", False)

    report = analyze_design(artwork, "image/png", use_ai=True)
    assert report.ai is None
    assert 0 <= report.compatibility_score <= 100
    assert report.verdict and report.colors and report.recommendations
    assert report.ai_status["reason"] == "ai_disabled"

    trace = trace_image(artwork, TraceOptions(max_colors=4, target_width_mm=80))
    pattern = digitize_shapes(trace.shapes, DigitizeOptions(target_width_mm=80)).pattern
    assert pattern.stitch_count > 0
    assert write(pattern, "dst")

    quote = calculate(PricingInputs(stitch_count=pattern.stitch_count, color_count=3, quantity=12))
    assert quote.unit_retail > 0


@requires_db
def test_the_score_is_identical_with_and_without_ai(artwork, monkeypatch, ai_on, stub,
                                                    db_session, org):
    """If the model could move the score, a quote would change because a model
    felt different today."""
    from app.core.config import settings
    from app.services.analyzer import analyze_design

    stub()
    with_ai = analyze_design(
        artwork, "image/png", use_ai=True, ai_context=context_for(db_session, org)
    )
    monkeypatch.setattr(settings, "ai_enabled", False)
    without_ai = analyze_design(artwork, "image/png", use_ai=True)

    assert with_ai.compatibility_score == without_ai.compatibility_score
    assert with_ai.verdict == without_ai.verdict
    assert [c["hex"] for c in with_ai.colors] == [c["hex"] for c in without_ai.colors]
    assert with_ai.suggested_fabric == without_ai.suggested_fabric


def test_no_provider_configured_is_a_normal_state(artwork, monkeypatch):
    from app.core.config import settings
    from app.services.analyzer import analyze_design

    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "")

    report = analyze_design(artwork, "image/png", use_ai=True)
    assert report.ai is None
    assert report.compatibility_score >= 0
    assert report.ai_status["reason"] == "no_provider"


# ============================================================== 11. beta mode
def test_beta_cost_mode_tightens_the_budget(monkeypatch):
    from app.ai.operations import VISION_ANALYSIS, operation
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_max_output_tokens", 4000)
    monkeypatch.setattr(settings, "ai_max_attempts", 6)

    monkeypatch.setattr(settings, "ai_beta_cost_mode", True)
    strict = operation(VISION_ANALYSIS)
    monkeypatch.setattr(settings, "ai_beta_cost_mode", False)
    relaxed = operation(VISION_ANALYSIS)

    assert strict.max_output_tokens < relaxed.max_output_tokens
    assert strict.max_attempts < relaxed.max_attempts


def test_beta_defaults_are_conservative():
    """A fresh deployment must be cheap before anyone has thought about it."""
    from app.core.config import Settings

    fresh = Settings()
    assert fresh.ai_beta_cost_mode is True
    assert fresh.ai_cache_enabled is True
    assert fresh.ai_budget_override is False
    assert 0 < fresh.ai_monthly_cost_per_tenant_usd <= 100
    assert 0 < fresh.ai_monthly_tokens_per_tenant
    assert 0 < fresh.ai_max_output_tokens <= 1000
    assert fresh.ai_image_max_edge_px <= 1536


# ============================================================== 12. dashboard
@requires_db
def test_the_dashboard_separates_recorded_from_calculated(
    app_client, artwork, ai_on, stub, quota_neutral
):
    stub()
    app_client.post(
        "/api/v1/designs/upload",
        files={"file": ("logo.png", artwork, "image/png")},
        data={"name": "Cost dashboard design", "analyze": "true", "use_ai": "true"},
    )

    response = app_client.get("/api/v1/ai/costs?period=month")
    assert response.status_code == 200
    body = response.json()

    assert set(body) >= {"recorded", "calculated", "pricing", "budgets", "budget_status"}
    assert body["recorded"]["total_ledger_rows"] >= 1
    assert "estimated_cost_usd" in body["recorded"]
    for metric in body["calculated"].values():
        if isinstance(metric, dict):
            assert "formula" in metric, "a calculated metric did not show its arithmetic"


@requires_db
def test_the_dashboard_labels_costs_as_estimates(app_client):
    body = app_client.get("/api/v1/ai/costs").json()
    assert body["pricing"]["confidence"] in ("estimate", "unknown")
    assert "estimate" in body["pricing"]["disclaimer"].lower()


@requires_db
def test_a_calculated_metric_with_no_data_is_null_not_zero(app_client):
    """Reporting $0.00 per design when no design exists is a fabricated
    financial result. Null is the honest answer."""
    body = app_client.get("/api/v1/ai/costs?period=day").json()
    for metric in body["calculated"].values():
        if isinstance(metric, dict) and metric.get("denominator") == 0:
            assert metric["value"] is None
            assert metric["computable"] is False


@requires_db
def test_global_figures_require_a_listed_administrator(app_client):
    response = app_client.get("/api/v1/ai/costs?scope=global")
    assert response.status_code == 403
    assert "AI_ADMIN_EMAILS" in response.json()["detail"]


@requires_db
def test_the_inventory_endpoint_lists_every_operation(app_client):
    body = app_client.get("/api/v1/ai/inventory").json()
    assert [op["operation"] for op in body["operations"]] == ["vision_analysis"]
    assert body["routing"]
    assert any(
        row["capability"] == "pricing_and_quotes"
        for row in body["deterministic_by_design"]
    )
    assert "AI_ENABLED" in body["kill_switches"]


# ========================================================== 13. end to end
@requires_db
def test_uploading_the_same_artwork_twice_only_pays_once(
    app_client, ai_on, stub, quota_neutral
):
    """The whole system, through the API: two uploads of one logo, one bill.

    The artwork is unique to this test — the shared fixture has already been
    analysed by earlier tests in this session, and a cache hit from *that* would
    make this pass without proving anything.
    """
    unique = Image.new("RGB", (820, 640), (255, 255, 255))
    unique.paste(Image.new("RGB", (300, 220), (77, 13, 199)), (60, 60))
    unique.paste(Image.new("RGB", (240, 180), (13, 199, 120)), (430, 300))
    buffer = io.BytesIO()
    unique.save(buffer, format="PNG")
    payload = buffer.getvalue()

    provider = stub()
    created = []
    for index in range(2):
        response = app_client.post(
            "/api/v1/designs/upload",
            # Different filenames and design names: only the pixels are shared,
            # so nothing but the artwork hash can produce the hit.
            files={"file": (f"logo-{index}.png", payload, "image/png")},
            data={"name": f"Repeat {index}", "analyze": "true", "use_ai": "true"},
        )
        assert response.status_code == 201
        created.append(response.json()["id"])

    assert len(provider.calls) == 1, "the repeat upload paid a second time"

    first, second = (app_client.get(f"/api/v1/designs/{i}").json() for i in created)
    assert first["analysis"]["ai_status"]["status"] == "ok"
    assert second["analysis"]["ai_status"]["status"] == "cached"
    assert second["analysis"]["ai"] == first["analysis"]["ai"]

    for design_id in created:
        app_client.delete(f"/api/v1/designs/{design_id}")
