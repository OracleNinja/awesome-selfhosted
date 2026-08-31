"""Gate behaviour.

Each test makes exactly one thing wrong with an otherwise valid book and
asserts the gate catches that one thing, so a passing test means the gate
discriminates rather than merely rejects everything.
"""

from __future__ import annotations

import pytest

from kdp.errors import ModelError
from kdp.gates import (
    metadata_compliance,
    originality,
    print_preflight,
    provenance_complete,
    research_hygiene,
    spec_legality,
)
from kdp.gates.base import Status
from kdp.models import (
    AIRole,
    AssetKind,
    AssetProvenance,
    BookManifest,
    MarketSignal,
    PageSpec,
    ResearchBrief,
    content_hash,
)
from conftest import make_assets, make_metadata, make_pages, make_spec


def codes(result) -> set[str]:
    return {f.code for f in result.findings}


# --- G1 research hygiene ----------------------------------------------------


def test_clean_brief_passes(brief):
    assert research_hygiene.run(brief).status is Status.PASS


def test_brief_without_signals_fails(brief):
    result = research_hygiene.run(ResearchBrief("b", "seg", opportunity="gap"))
    assert result.status is Status.FAIL
    assert "research.no_signals" in codes(result)


def test_overlong_observation_is_blocked(brief):
    signal = MarketSignal("s", "seg", observations=("x" * 500,))
    result = research_hygiene.run(
        ResearchBrief("b", "seg", signals=(signal,), opportunity="gap")
    )
    assert result.status is Status.FAIL
    assert "research.observation_too_long" in codes(result)


def test_media_reference_in_an_observation_is_blocked():
    signal = MarketSignal("s", "seg", observations=("see cover-art.png for style",))
    result = research_hygiene.run(
        ResearchBrief("b", "seg", signals=(signal,), opportunity="gap")
    )
    assert result.status is Status.FAIL
    assert "research.asset_reference" in codes(result)


def test_quoted_observation_reads_as_verbatim_and_is_blocked():
    signal = MarketSignal("s", "seg", observations=('"the best coloring book ever"',))
    result = research_hygiene.run(
        ResearchBrief("b", "seg", signals=(signal,), opportunity="gap")
    )
    assert result.status is Status.FAIL
    assert "research.possible_verbatim" in codes(result)


def test_signal_rejects_fields_outside_the_research_whitelist():
    # The structural guardrail: competitor artwork cannot even be represented.
    with pytest.raises(ModelError, match="whitelist"):
        MarketSignal.from_dict(
            {"signal_id": "s", "segment": "seg", "cover_image_url": "http://x/a.png"}
        )


def test_missing_opportunity_is_major_not_fatal(brief):
    from dataclasses import replace

    result = research_hygiene.run(replace(brief, opportunity=""))
    assert "research.no_opportunity" in codes(result)
    assert result.status is Status.PASS


# --- G2 spec legality -------------------------------------------------------


def test_valid_spec_passes_when_tables_are_trusted():
    spec = make_spec(art_count=12, page_count=26)
    result = spec_legality.run(spec, require_verified_specs=False)
    assert result.status is Status.PASS, result.to_json()


def test_verified_spec_tables_no_longer_stop_the_book():
    """The geometry tables were verified against KDP on 2026-08-31."""
    spec = make_spec(art_count=12, page_count=26)
    result = spec_legality.run(spec)
    assert "spec.tables_unverified" not in codes(result)
    assert result.status is Status.PASS, result.to_json()


def test_an_unverified_geometry_table_would_still_stop_the_book(monkeypatch):
    """The mechanism still bites; it is the data that changed, not the rule."""
    from kdp.specs import revision

    monkeypatch.setattr(
        revision,
        "SOURCES",
        {**revision.SOURCES, "cover": revision.SourceRecord("cover")},
    )
    result = spec_legality.run(make_spec(art_count=12, page_count=26))
    assert result.status is Status.FAIL
    assert "spec.tables_unverified" in codes(result)


def test_page_count_below_the_printable_minimum_fails():
    spec = make_spec(art_count=4, page_count=10)
    result = spec_legality.run(spec, require_verified_specs=False)
    assert "spec.page_count_out_of_range" in codes(result)


def test_declared_and_described_page_counts_must_agree():
    spec = make_spec(art_count=12, page_count=99)
    result = spec_legality.run(spec, require_verified_specs=False)
    assert "spec.page_count_mismatch" in codes(result)


def test_non_contiguous_page_indices_fail():
    pages = (PageSpec("a", 1, "art"), PageSpec("b", 3, "art"))
    spec = make_spec(pages=pages, page_count=2)
    result = spec_legality.run(spec, require_verified_specs=False)
    assert "spec.page_indices_not_contiguous" in codes(result)


def test_duplicate_page_ids_fail():
    pages = (PageSpec("same", 1, "art"), PageSpec("same", 2, "art"))
    spec = make_spec(pages=pages, page_count=2)
    result = spec_legality.run(spec, require_verified_specs=False)
    assert "spec.duplicate_page_ids" in codes(result)


def test_single_sided_art_on_an_odd_page_is_flagged():
    # Art on a recto backs onto another drawing; marker bleed ruins both.
    pages = (PageSpec("art-a", 1, "art"), PageSpec("blank", 2, "blank"))
    spec = make_spec(pages=pages, page_count=2, single_sided_art=True)
    result = spec_legality.run(spec, require_verified_specs=False)
    assert "spec.single_sided_violated" in codes(result)


def test_unknown_trim_fails_the_gate_rather_than_raising():
    spec = make_spec(art_count=12, page_count=26, trim="9x9")
    result = spec_legality.run(spec, require_verified_specs=False)
    assert result.status is Status.FAIL
    assert "spec.unknown_trim" in codes(result)


def test_non_standard_trim_is_reported_but_not_fatal():
    spec = make_spec(art_count=12, page_count=26, trim="8.5x8.5")
    result = spec_legality.run(spec, require_verified_specs=False)
    assert "spec.non_standard_trim" in codes(result)
    assert result.status is Status.PASS


def test_passing_spec_records_geometry_as_evidence():
    spec = make_spec(art_count=12, page_count=26)
    result = spec_legality.run(spec, require_verified_specs=False)
    assert result.evidence["cover"]["spine_width_in"] > 0
    assert result.evidence["interior"]["gutter_in"] == 0.375


# --- G3 provenance completeness ---------------------------------------------


def test_complete_provenance_passes(manifest):
    assert provenance_complete.run(manifest).status is Status.PASS


def test_art_page_without_a_record_fails(manifest):
    stripped = manifest.with_assets(manifest.assets[:-1])
    result = provenance_complete.run(stripped)
    assert result.status is Status.FAIL
    assert "provenance.missing_record" in codes(result)


def test_unclassified_asset_fails(manifest):
    spec = manifest.spec
    result = provenance_complete.run(
        manifest.with_assets(make_assets(spec, ai_role=AIRole.UNKNOWN))
    )
    assert result.status is Status.FAIL
    assert "provenance.unclassified" in codes(result)


def test_generated_asset_without_a_prompt_version_is_flagged(manifest):
    downgraded = tuple(
        AssetProvenance(
            a.asset_id, a.kind, a.ai_role, a.content_hash, tool=a.tool
        )
        for a in manifest.assets
    )
    result = provenance_complete.run(manifest.with_assets(downgraded))
    assert "provenance.no_prompt_version" in codes(result)


def test_orphan_image_is_reported_without_stopping_the_book(manifest):
    extra = AssetProvenance(
        "stray", AssetKind.IMAGE, AIRole.NONE, content_hash(b"stray")
    )
    result = provenance_complete.run(manifest.with_assets(manifest.assets + (extra,)))
    assert "provenance.orphan_asset" in codes(result)
    assert result.status is Status.PASS


# --- G4 originality ---------------------------------------------------------


def test_distinct_pages_pass_both_duplication_checks(built_manifest):
    manifest, assets_dir = built_manifest
    result = originality.run(manifest, assets_dir=assets_dir)
    assert result.status is Status.PASS, result.to_json()
    assert result.evidence["pages_hashed"] == len(manifest.spec.art_pages)


def test_repeated_page_inside_a_book_fails(built_manifest):
    manifest, assets_dir = built_manifest
    last = manifest.assets[-1]
    from dataclasses import replace

    duplicated = manifest.assets[:-1] + (
        replace(last, content_hash=manifest.assets[0].content_hash),
    )
    result = originality.run(
        manifest.with_assets(duplicated), assets_dir=assets_dir
    )
    assert result.status is Status.FAIL
    assert "originality.intra_book_duplicate" in codes(result)


def test_a_rescaled_repeat_is_caught_by_perceptual_hashing(built_manifest):
    """Different bytes, same drawing — invisible to an exact-match check."""
    from dataclasses import replace

    from kdp.models import content_hash
    from kdp.providers.mock import _png

    manifest, assets_dir = built_manifest
    first, second = manifest.spec.art_pages[0], manifest.spec.art_pages[1]

    # Redraw page one's artwork smaller, keeping its aspect ratio — which is
    # what a genuine rescaled repeat looks like — and put it on page two.
    original = manifest.approved_for(first.page_id)
    width = original.width * 3 // 4
    height = original.height * 3 // 4
    rescaled = _png(width, height, first.page_id.encode())
    (assets_dir / f"{second.page_id}.png").write_bytes(rescaled)
    updated = tuple(
        replace(a, content_hash=content_hash(rescaled), width=width, height=height)
        if a.page == second.page_id
        else a
        for a in manifest.assets
    )

    result = originality.run(manifest.with_assets(updated), assets_dir=assets_dir)
    assert result.status is Status.FAIL
    assert "originality.near_duplicate" in codes(result)
    # The bytes differ, so only the perceptual check could have found it.
    assert "originality.intra_book_duplicate" not in codes(result)


def test_page_reused_from_a_published_book_fails(built_manifest):
    manifest, assets_dir = built_manifest
    catalogue = {manifest.assets[0].content_hash: "book-000"}
    result = originality.run(
        manifest, catalogue_hashes=catalogue, assets_dir=assets_dir
    )
    assert result.status is Status.FAIL
    assert "originality.catalogue_duplicate" in codes(result)


def test_a_catalogue_near_duplicate_is_caught(built_manifest):
    from kdp.inspection.image import perceptual_hash

    manifest, assets_dir = built_manifest
    first = manifest.spec.art_pages[0].page_id
    prior = str(perceptual_hash(assets_dir / f"{first}.png"))

    result = originality.run(
        manifest, catalogue_phashes={prior: "book-000"}, assets_dir=assets_dir
    )
    assert result.status is Status.FAIL
    assert "originality.catalogue_near_duplicate" in codes(result)


def test_the_book_s_own_prior_hashes_are_not_self_duplicates(built_manifest):
    manifest, assets_dir = built_manifest
    catalogue = {manifest.assets[0].content_hash: manifest.book_id}
    result = originality.run(
        manifest, catalogue_hashes=catalogue, assets_dir=assets_dir
    )
    assert result.status is Status.PASS


def test_unreadable_pages_block_rather_than_pass(manifest):
    # The safety property: a page nobody could read must never read as clean.
    result = originality.run(manifest, require_perceptual=True)
    assert result.status is Status.BLOCKED
    assert result.status.allows_advance is False
    assert "could not be read" in result.findings[0].message


def test_skipping_the_perceptual_check_blocks(built_manifest):
    manifest, assets_dir = built_manifest
    result = originality.run(
        manifest, require_perceptual=False, assets_dir=assets_dir
    )
    assert result.status is Status.BLOCKED
    assert "skipped by request" in result.findings[0].message


# --- G5 print preflight -----------------------------------------------------


def test_preflight_blocks_without_pdfs(manifest):
    result = print_preflight.run(manifest)
    assert result.status is Status.BLOCKED
    assert "Deep preflight needs both" in result.findings[0].message


def test_preflight_reports_a_missing_declared_file(tmp_path, manifest):
    result = print_preflight.run(
        manifest,
        interior_pdf=tmp_path / "absent-interior.pdf",
        cover_pdf=tmp_path / "absent-cover.pdf",
    )
    assert "preflight.file_missing" in codes(result)
    # FAIL when the PDF toolchain is installed, BLOCKED when it is not. Either
    # way the book stops, which is the property worth asserting — the test
    # should not depend on what happens to be installed.
    assert result.status.allows_advance is False


def test_preflight_flags_a_book_with_no_art():
    # Printable page count, but nothing to colour.
    pages = tuple(PageSpec(f"blank-{i}", i + 1, "blank") for i in range(26))
    spec = make_spec(pages=pages)
    result = print_preflight.run(BookManifest(spec=spec))
    assert "preflight.no_art_pages" in codes(result)


def test_preflight_blocks_when_geometry_cannot_be_resolved():
    spec = make_spec(art_count=12, trim="9x9")
    result = print_preflight.run(BookManifest(spec=spec))
    assert result.status is Status.BLOCKED
    assert "Cannot resolve print geometry" in result.findings[0].message


# --- G6 metadata compliance -------------------------------------------------


def test_complete_metadata_passes(manifest):
    assert metadata_compliance.run(manifest).status is Status.PASS


def test_missing_metadata_blocks(manifest):
    from dataclasses import replace

    result = metadata_compliance.run(replace(manifest, metadata=None))
    assert result.status is Status.BLOCKED


def test_unclassified_asset_blocks_the_disclosure_answer(manifest):
    spec = manifest.spec
    result = metadata_compliance.run(
        manifest.with_assets(make_assets(spec, ai_role=AIRole.UNKNOWN))
    )
    assert result.status is Status.FAIL
    assert "metadata.disclosure_incomplete" in codes(result)


def test_ai_generated_book_needs_a_confirmed_disclosure(manifest):
    from dataclasses import replace

    unconfirmed = replace(manifest, metadata=make_metadata(ai_disclosure_confirmed=False))
    result = metadata_compliance.run(unconfirmed)
    assert result.status is Status.FAIL
    assert "metadata.disclosure_unconfirmed" in codes(result)


def test_non_ai_book_needs_no_disclosure_confirmation(manifest):
    from dataclasses import replace

    hand_drawn = replace(
        manifest,
        assets=make_assets(manifest.spec, ai_role=AIRole.NONE),
        metadata=make_metadata(ai_disclosure_confirmed=False),
    )
    result = metadata_compliance.run(hand_drawn)
    assert result.status is Status.PASS


def test_keyword_stuffing_fails(manifest):
    from dataclasses import replace

    stuffed = replace(
        manifest,
        metadata=make_metadata(
            keywords=("coloring, book, adult, relax, calm, zen, art, flowers",)
        ),
    )
    result = metadata_compliance.run(stuffed)
    assert result.status is Status.FAIL
    assert "metadata.keyword_stuffing" in codes(result)


def test_too_many_keyword_slots_fail(manifest):
    from dataclasses import replace

    result = metadata_compliance.run(
        replace(manifest, metadata=make_metadata(keywords=tuple(f"k{i}" for i in range(9))))
    )
    assert "metadata.too_many_keywords" in codes(result)


def test_repeated_keyword_slots_are_flagged(manifest):
    from dataclasses import replace

    result = metadata_compliance.run(
        replace(manifest, metadata=make_metadata(keywords=("garden", "Garden ")))
    )
    assert "metadata.duplicate_keywords" in codes(result)


def test_thin_description_is_flagged(manifest):
    from dataclasses import replace

    result = metadata_compliance.run(
        replace(manifest, metadata=make_metadata(description="Short."))
    )
    assert "metadata.description_too_short" in codes(result)


def test_evidence_carries_a_human_readable_disclosure_statement(manifest):
    result = metadata_compliance.run(manifest)
    assert "Disclose to KDP for: image" in result.evidence["disclosure_statement"]
