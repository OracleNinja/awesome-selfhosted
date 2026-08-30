"""The subsystems added on top of the foundation.

Several tests here force a dependency to look missing rather than waiting for
an environment that lacks it. The fail-closed behaviour is the most important
property in the system and it must be tested wherever the suite runs — not only
on the machines that happen to have nothing installed.
"""

from __future__ import annotations

from datetime import date

import pytest

from kdp.errors import ModelError
from kdp.gates import asset_qa, cover_qa, interior_qa, kdp_compliance, originality
from kdp.gates.base import Status
from kdp.models import (
    ApprovalRecord,
    ApprovalStatus,
    Artifact,
    ArtifactKind,
    AssetPrompt,
    BookStrategy,
    Claim,
    Confidence,
    PricingScenario,
    PromptPlan,
    compare,
    default_assumptions,
)
from kdp.models.economics import Assumption
from kdp.providers import ProviderUnavailable, get_provider
from kdp.providers.base import GenerationRequest, GenerationResult, ProviderError
from kdp.providers.mock import MockImageProvider
from kdp.specs.revision import (
    SOURCES,
    SourceRecord,
    is_verified,
    source,
    stale_tables,
    unverified_tables,
    verification_problem,
)


# --- specification verification and staleness -------------------------------


def test_tables_ship_unverified():
    # The shipped state. Flipping this without checking the primary sources is
    # the mistake the whole mechanism exists to make difficult.
    assert is_verified() is False
    assert set(unverified_tables()) == set(SOURCES)


def test_a_source_with_url_and_date_is_verified():
    record = SourceRecord("trim", source_url="https://kdp.amazon.com/x", verified_on="2026-08-01")
    assert record.state == "VERIFIED"


def test_a_source_missing_either_half_is_unverified():
    assert SourceRecord("trim", source_url="https://x").state == "UNVERIFIED"
    assert SourceRecord("trim", verified_on="2026-08-01").state == "UNVERIFIED"


def test_an_old_verification_goes_stale():
    record = SourceRecord("trim", source_url="https://x", verified_on="2020-01-01")
    assert record.state == "VERIFIED"
    assert record.is_stale(date(2026, 8, 30)) is True
    assert record.is_stale(date(2020, 3, 1)) is False


def test_an_unverified_table_is_not_reported_as_stale():
    # Different questions: never checked, versus checked and now old.
    assert SourceRecord("trim").is_stale(date(2026, 8, 30)) is False


def test_an_unparseable_date_counts_as_stale():
    # A malformed record is not evidence of freshness.
    record = SourceRecord("trim", source_url="https://x", verified_on="last tuesday")
    assert record.is_stale(date(2026, 8, 30)) is True


def test_verification_problem_names_the_tables():
    problem = verification_problem("trim", "paper")
    assert problem is not None
    assert "trim" in problem and "paper" in problem


def test_verification_problem_is_none_when_everything_checks_out(monkeypatch):
    fresh = {
        name: SourceRecord(name, source_url="https://kdp.amazon.com/x", verified_on="2026-08-01")
        for name in SOURCES
    }
    monkeypatch.setattr("kdp.specs.revision.SOURCES", fresh)
    assert verification_problem(today=date(2026, 8, 15)) is None
    assert is_verified(today=date(2026, 8, 15)) is True


def test_unknown_table_is_treated_as_unverified():
    assert source("nonexistent").state == "UNVERIFIED"


# --- forced BLOCKED paths ---------------------------------------------------


def test_originality_blocks_when_a_page_cannot_be_read(manifest):
    result = originality.run(manifest, require_perceptual=True)
    assert result.status is Status.BLOCKED
    assert result.status.allows_advance is False
    assert "could not be read" in result.findings[0].message


def test_an_unreadable_format_blocks_without_the_optional_decoder(tmp_path, monkeypatch):
    """The genuine missing-capability case: a format the stdlib cannot decode."""
    from kdp.inspection import image as image_module

    monkeypatch.setattr(image_module, "_pillow_available", lambda: False)
    target = tmp_path / "page.jpg"
    target.write_bytes(b"\xff\xd8\xff\xe0not-really-a-jpeg")

    with pytest.raises(image_module.ImageLoadError, match="Pillow is not installed"):
        image_module.inspect_image(target)


def test_asset_qa_blocks_when_a_page_cannot_be_inspected(manifest):
    """The fixture's provenance points at files that do not exist."""
    result = asset_qa.run(manifest, require_pixel_inspection=True)
    assert result.status is Status.BLOCKED
    # A blocked gate must say what went unchecked, not just that it stopped.
    message = result.findings[0].message
    assert "could not be inspected" in message
    assert "pure white background" in message


def test_asset_qa_blocks_when_inspection_is_skipped(manifest):
    result = asset_qa.run(manifest, require_pixel_inspection=False)
    assert result.status is Status.BLOCKED
    assert "skipped by request" in result.findings[0].message


def test_interior_qa_blocks_when_the_pdf_reader_is_missing(manifest, monkeypatch, tmp_path):
    pdf = tmp_path / "interior.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    staged = manifest.with_artifact(Artifact.from_file(ArtifactKind.INTERIOR_PDF, pdf))
    monkeypatch.setattr(interior_qa, "pdf_reader_available", lambda: False)
    result = interior_qa.run(staged)
    assert result.status is Status.BLOCKED


def test_cover_qa_blocks_when_the_pdf_reader_is_missing(manifest, monkeypatch, tmp_path):
    pdf = tmp_path / "cover.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    staged = manifest.with_artifact(Artifact.from_file(ArtifactKind.COVER_PDF, pdf))
    monkeypatch.setattr(cover_qa, "pdf_reader_available", lambda: False)
    assert cover_qa.run(staged).status is Status.BLOCKED


def test_compliance_blocks_on_unverified_policy(manifest):
    assert kdp_compliance.run(manifest).status is Status.BLOCKED


def test_interior_qa_catches_a_pdf_edited_after_the_build(manifest, tmp_path):
    """The failure a hash comparison exists to find."""
    pdf = tmp_path / "interior.pdf"
    pdf.write_bytes(b"%PDF-1.4\noriginal\n")
    staged = manifest.with_artifact(Artifact.from_file(ArtifactKind.INTERIOR_PDF, pdf))
    pdf.write_bytes(b"%PDF-1.4\nrebuilt\n")

    result = interior_qa.run(staged)
    assert "interior_qa.artifact_changed" in {f.code for f in result.findings}
    assert result.status.allows_advance is False


def test_cover_qa_catches_an_interior_rebuilt_after_the_cover(manifest, tmp_path):
    """A page-count change after the cover is built leaves the spine wrong."""
    cover = tmp_path / "cover.pdf"
    cover.write_bytes(b"%PDF-1.4\ncover\n")
    interior = tmp_path / "interior.pdf"
    interior.write_bytes(b"%PDF-1.4\ninterior\n")

    staged = manifest.with_artifact(
        Artifact.from_file(ArtifactKind.COVER_PDF, cover, created_at="2026-01-01T00:00:00+00:00")
    ).with_artifact(
        Artifact.from_file(
            ArtifactKind.INTERIOR_PDF, interior, created_at="2026-01-02T00:00:00+00:00"
        )
    )
    result = cover_qa.run(staged)
    assert "cover_qa.stale_against_interior" in {f.code for f in result.findings}


# --- economics --------------------------------------------------------------


def test_net_royalty_is_gross_minus_print_cost():
    scenario = PricingScenario("t", 9.99, 100, royalty_rate=0.6,
                               fixed_cost_usd=1.0, per_page_cost_usd=0.012)
    assert scenario.print_cost_usd == pytest.approx(2.2)
    assert scenario.gross_royalty_usd == pytest.approx(5.994)
    assert scenario.net_royalty_usd == pytest.approx(3.794)
    assert scenario.viable is True


def test_a_price_that_loses_money_is_not_viable():
    # A long book at a low price: the print cost eats the royalty.
    scenario = PricingScenario("cheap", 4.99, 400)
    assert scenario.net_royalty_usd < 0
    assert scenario.viable is False


def test_print_cost_scales_with_page_count():
    short = PricingScenario("a", 9.99, 50)
    long = PricingScenario("b", 9.99, 300)
    assert long.print_cost_usd > short.print_cost_usd
    assert long.net_royalty_usd < short.net_royalty_usd


def test_compare_builds_one_scenario_per_price_in_order():
    econ = compare([9.99, 5.99, 7.99], 60)
    assert [s.label for s in econ.scenarios] == ["$5.99", "$7.99", "$9.99"]


def test_no_price_is_privileged():
    # $5.99 is a scenario like any other; nothing in the model favours it.
    econ = compare([5.99, 12.99], 60)
    assert econ.selected is None


def test_confidence_is_the_weakest_assumption():
    econ = compare([7.99], 60, assumptions=(
        Assumption("a", 1.0, Confidence.OBSERVED),
        Assumption("b", 2.0, Confidence.ESTIMATED),
    ))
    assert econ.confidence is Confidence.ESTIMATED


def test_default_assumptions_are_unknown_until_the_royalty_table_is_checked():
    econ = compare([7.99], 60)
    assert econ.confidence is Confidence.UNKNOWN
    assert all(a.confidence is Confidence.UNKNOWN for a in default_assumptions())


def test_a_scenario_needs_a_positive_price_and_page_count():
    with pytest.raises(ModelError):
        PricingScenario("bad", 0.0, 60)
    with pytest.raises(ModelError):
        PricingScenario("bad", 7.99, 0)


# --- providers --------------------------------------------------------------


def test_mock_provider_is_deterministic():
    provider = MockImageProvider()
    request = GenerationRequest("P1", "pr", "v1", "a fern", 64, 64)
    assert provider.generate(request).data == provider.generate(request).data


def test_mock_provider_varies_by_prompt_and_attempt():
    provider = MockImageProvider()
    a = provider.generate(GenerationRequest("P1", "pr", "v1", "a fern", 64, 64))
    b = provider.generate(GenerationRequest("P1", "pr", "v1", "a thistle", 64, 64))
    c = provider.generate(GenerationRequest("P1", "pr", "v1", "a fern", 64, 64, attempt=2))
    assert a.data != b.data != c.data


def test_a_failed_result_must_carry_a_reason():
    with pytest.raises(ProviderError, match="carries no reason"):
        GenerationResult(ok=False, page_id="P1")


def test_a_successful_result_must_carry_data():
    with pytest.raises(ProviderError, match="carries no data"):
        GenerationResult(ok=True, page_id="P1")


def test_higgsfield_is_unavailable_and_says_why(monkeypatch):
    provider = get_provider("higgsfield")
    assert provider.available() is False
    assert "MCP" in provider.unavailable_reason()
    with pytest.raises(ProviderUnavailable):
        provider.generate(GenerationRequest("P1", "pr", "v1", "x", 64, 64))


def test_higgsfield_http_mode_reports_a_missing_credential(monkeypatch):
    monkeypatch.setenv("KDP_HIGGSFIELD_MODE", "http")
    monkeypatch.delenv("HIGGSFIELD_API_KEY", raising=False)
    monkeypatch.delenv("KDP_HIGGSFIELD_API_KEY", raising=False)
    provider = get_provider("higgsfield")
    assert provider.available() is False
    assert "no credential is present" in provider.unavailable_reason()


def test_a_credential_never_reaches_a_result_or_provenance(monkeypatch):
    monkeypatch.setenv("HIGGSFIELD_API_KEY", "sk-secret-value")
    result = MockImageProvider().generate(
        GenerationRequest("P1", "pr", "v1", "a fern", 64, 64)
    )
    serialised = repr(result.meta) + result.provider + result.model
    assert "sk-secret-value" not in serialised


def test_unknown_provider_names_the_known_ones():
    with pytest.raises(ProviderError, match="known providers"):
        get_provider("dall-e-42")


# --- strategy and prompts ---------------------------------------------------


def test_estimated_confidence_is_not_evidence():
    assert Confidence.ESTIMATED.is_evidence is False
    assert Confidence.UNKNOWN.is_evidence is False
    assert Confidence.OBSERVED.is_evidence is True
    assert Confidence.INFERRED.is_evidence is True


def test_a_claim_defaults_to_unknown_confidence():
    assert Claim("x", "y").confidence is Confidence.UNKNOWN


def test_prompt_hash_is_stable_across_serialisation():
    prompt = AssetPrompt("P", "PAGE-001", "v1", "a fern", composition="centred")
    assert AssetPrompt.from_dict(prompt.to_dict()).prompt_hash == prompt.prompt_hash


def test_prompts_differing_only_in_subject_hash_differently():
    a = AssetPrompt("P1", "PAGE-001", "v1", "a fern")
    b = AssetPrompt("P2", "PAGE-002", "v1", "a thistle")
    assert a.prompt_hash != b.prompt_hash


def test_prompt_rejects_an_out_of_range_complexity():
    with pytest.raises(ModelError, match="scale is 1-5"):
        AssetPrompt("P", "PAGE-001", "v1", "x", complexity=9)


def test_plan_finds_the_prompt_for_a_page():
    plan = PromptPlan("plan", prompts=(AssetPrompt("P1", "PAGE-001", "v1", "a fern"),))
    assert plan.for_page("PAGE-001").prompt_id == "P1"
    assert plan.for_page("PAGE-999") is None


def test_strategy_requires_an_id():
    with pytest.raises(ModelError):
        BookStrategy(strategy_id="", brief_id="b", niche="n", audience="a")


# --- approval ---------------------------------------------------------------


def test_an_approval_decision_needs_a_reviewer():
    with pytest.raises(ModelError, match="names no reviewer"):
        ApprovalRecord(status=ApprovalStatus.APPROVED, book_fingerprint="sha256:x")


def test_an_approval_decision_needs_a_fingerprint():
    with pytest.raises(ModelError, match="records no book fingerprint"):
        ApprovalRecord(status=ApprovalStatus.APPROVED, reviewer="someone")


def test_a_pending_record_needs_neither():
    assert ApprovalRecord().status is ApprovalStatus.PENDING


def test_approval_is_valid_only_for_its_own_fingerprint():
    record = ApprovalRecord(
        status=ApprovalStatus.APPROVED, reviewer="r", book_fingerprint="sha256:aaa"
    )
    assert record.is_valid_for("sha256:aaa") is True
    assert record.is_valid_for("sha256:bbb") is False


def test_a_rejection_is_never_valid_even_at_the_right_fingerprint():
    record = ApprovalRecord(
        status=ApprovalStatus.REJECTED, reviewer="r", book_fingerprint="sha256:aaa"
    )
    assert record.is_valid_for("sha256:aaa") is False


# --- what the generator will and will not pay for ---------------------------


def _fresh_book(tmp_path):
    from kdp.generation import generate_book
    from fixtures import build_manifest

    manifest = build_manifest()
    outcome = generate_book(
        manifest, MockImageProvider(max_dimension=32), tmp_path / "assets",
        now="2026-01-01T00:00:00+00:00",
    )
    return outcome.manifest


def test_a_pending_page_is_not_regenerated(tmp_path):
    """The expensive mistake: paying twice for a page QA has not judged yet."""
    from kdp.generation import generate_book

    first = _fresh_book(tmp_path)
    assert all(a.status.value == "pending" for a in first.assets)

    again = generate_book(
        first, MockImageProvider(max_dimension=32), tmp_path / "assets",
        now="2026-01-01T01:00:00+00:00",
    )
    assert again.generated == ()
    assert len(again.skipped) == len(first.spec.art_pages)


def test_a_rejected_page_is_regenerated(tmp_path):
    from kdp.generation import generate_book, reject_asset

    first = _fresh_book(tmp_path)
    page = first.spec.art_pages[0].page_id
    rejected = reject_asset(first, first.attempts(page)[-1].asset_id, "stray shading")

    again = generate_book(
        rejected, MockImageProvider(max_dimension=32), tmp_path / "assets",
        now="2026-01-01T02:00:00+00:00",
    )
    assert again.generated == (page,)
    assert len(again.manifest.attempts(page)) == 2


def test_an_approved_page_is_never_regenerated(tmp_path):
    from kdp.generation import approve_asset, generate_book

    first = _fresh_book(tmp_path)
    page = first.spec.art_pages[0].page_id
    approved = approve_asset(first, first.attempts(page)[-1].asset_id)

    again = generate_book(
        approved, MockImageProvider(max_dimension=32), tmp_path / "assets",
        now="2026-01-01T03:00:00+00:00",
    )
    assert page in again.skipped


def test_approving_a_new_version_supersedes_the_old_one(tmp_path):
    from kdp.generation import approve_asset, generate_book, reject_asset

    first = _fresh_book(tmp_path)
    page = first.spec.art_pages[0].page_id
    v1 = first.attempts(page)[-1].asset_id

    approved = approve_asset(first, v1)
    rejected = reject_asset(approved, v1, "second thoughts")
    regenerated = generate_book(
        rejected, MockImageProvider(max_dimension=32), tmp_path / "assets",
        now="2026-01-01T04:00:00+00:00",
    ).manifest
    v2 = regenerated.attempts(page)[-1].asset_id
    final = approve_asset(regenerated, v2)

    assert final.approved_for(page).asset_id == v2
    # Exactly one version of a page may ship.
    assert len([a for a in final.attempts(page) if a.ships]) == 1


# --- approval invalidation, artifact by artifact ----------------------------


def _approved(manifest):
    from dataclasses import replace

    from kdp.models import ApprovalRecord, ApprovalStatus

    return replace(
        manifest,
        approval=ApprovalRecord(
            status=ApprovalStatus.APPROVED,
            reviewer="a.reviewer",
            decided_at="2026-01-02T00:00:00+00:00",
            book_fingerprint=manifest.production_fingerprint(),
        ),
    )


def test_approval_is_current_for_an_untouched_book(built_manifest):
    manifest, _ = built_manifest
    assert _approved(manifest).approval_is_current() is True


def test_changing_a_shipping_asset_invalidates_approval(built_manifest):
    from dataclasses import replace

    manifest, _ = built_manifest
    approved = _approved(manifest)
    edited = replace(
        approved,
        assets=(replace(approved.assets[0], content_hash="sha256:" + "0" * 64),)
        + approved.assets[1:],
    )
    assert edited.approval_is_current() is False


def test_changing_the_interior_pdf_invalidates_approval(built_manifest, tmp_path):
    from kdp.models import Artifact, ArtifactKind

    manifest, _ = built_manifest
    pdf = tmp_path / "interior.pdf"
    pdf.write_bytes(b"%PDF-1.4\noriginal\n")
    staged = manifest.with_artifact(Artifact.from_file(ArtifactKind.INTERIOR_PDF, pdf))
    approved = _approved(staged)

    pdf.write_bytes(b"%PDF-1.4\nrebuilt\n")
    rebuilt = approved.with_artifact(Artifact.from_file(ArtifactKind.INTERIOR_PDF, pdf))
    assert rebuilt.approval_is_current() is False


def test_changing_the_cover_pdf_invalidates_approval(built_manifest, tmp_path):
    from kdp.models import Artifact, ArtifactKind

    manifest, _ = built_manifest
    pdf = tmp_path / "cover.pdf"
    pdf.write_bytes(b"%PDF-1.4\noriginal\n")
    approved = _approved(
        manifest.with_artifact(Artifact.from_file(ArtifactKind.COVER_PDF, pdf))
    )
    pdf.write_bytes(b"%PDF-1.4\nrebuilt\n")
    rebuilt = approved.with_artifact(Artifact.from_file(ArtifactKind.COVER_PDF, pdf))
    assert rebuilt.approval_is_current() is False


def test_changing_metadata_invalidates_approval(built_manifest):
    from dataclasses import replace

    manifest, _ = built_manifest
    approved = _approved(manifest)
    for field, value in (
        ("title", "A Different Title"),
        ("description", "A different description entirely, at length." * 4),
        ("keywords", ("something", "else")),
        ("categories", ("A different category",)),
        ("author", "Someone Else"),
    ):
        edited = replace(
            approved, metadata=replace(approved.metadata, **{field: value})
        )
        assert edited.approval_is_current() is False, field


def test_changing_the_price_invalidates_approval(built_manifest):
    from dataclasses import replace

    from kdp.models import compare

    manifest, _ = built_manifest
    priced = replace(
        manifest,
        economics=replace(compare([5.99, 7.99], manifest.spec.page_count), selected="$5.99"),
    )
    approved = _approved(priced)
    assert approved.approval_is_current() is True

    repriced = replace(approved, economics=replace(approved.economics, selected="$7.99"))
    assert repriced.approval_is_current() is False


def test_changing_the_specification_invalidates_approval(built_manifest):
    from dataclasses import replace

    manifest, _ = built_manifest
    approved = _approved(manifest)
    retitled = replace(approved, spec=replace(approved.spec, title="Something Else"))
    assert retitled.approval_is_current() is False


def test_a_run_record_does_not_invalidate_approval(built_manifest):
    """The fingerprint covers the book, not the bookkeeping."""
    from kdp.models import RunRecord

    manifest, _ = built_manifest
    approved = _approved(manifest)
    logged = approved.with_run(
        RunRecord(run_id="r1", book_id=approved.book_id, stage="qa", status="pass")
    )
    assert logged.approval_is_current() is True


# --- explicit regeneration --------------------------------------------------


def test_regeneration_requires_naming_the_pages(tmp_path):
    """No redo-everything switch: naming pages is what makes a spend deliberate."""
    from kdp.generation import generate_book

    first = _fresh_book(tmp_path)
    page = first.spec.art_pages[0].page_id

    forced = generate_book(
        first, MockImageProvider(max_dimension=32), tmp_path / "assets",
        now="2026-01-01T05:00:00+00:00", regenerate=(page,),
    )
    assert forced.generated == (page,)
    # Everything not named is left alone.
    assert len(forced.skipped) == len(first.spec.art_pages) - 1


def test_regenerating_an_unknown_page_is_refused(tmp_path):
    from kdp.generation import generate_book
    from kdp.providers import ProviderUnavailable

    first = _fresh_book(tmp_path)
    with pytest.raises(ProviderUnavailable, match="does not have"):
        generate_book(
            first, MockImageProvider(max_dimension=32), tmp_path / "assets",
            regenerate=("PAGE-999",),
        )


def test_an_approved_page_is_regenerated_only_when_named(tmp_path):
    from kdp.generation import approve_asset, generate_book

    first = _fresh_book(tmp_path)
    page = first.spec.art_pages[0].page_id
    approved = approve_asset(first, first.attempts(page)[-1].asset_id)

    assert page in generate_book(
        approved, MockImageProvider(max_dimension=32), tmp_path / "assets",
        now="2026-01-01T06:00:00+00:00",
    ).skipped

    forced = generate_book(
        approved, MockImageProvider(max_dimension=32), tmp_path / "assets",
        now="2026-01-01T07:00:00+00:00", regenerate=(page,),
    )
    assert forced.generated == (page,)
