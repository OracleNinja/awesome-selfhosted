"""Real inspection of real files, valid and deliberately broken.

Every defect below is injected on purpose. A QA layer that has only ever seen
good input has not been tested — it has been demonstrated.
"""

from __future__ import annotations

import pytest

from kdp.assembly import build_cover, build_interior
from kdp.gates import asset_qa, cover_qa, interior_qa, print_preflight
from kdp.gates.base import Status
from kdp.inspection.checks import check_cover, check_interior
from kdp.inspection.image import (
    MAX_MIDTONE_FRACTION,
    ImageLoadError,
    hamming,
    inspect_image,
    load_image,
    perceptual_hash,
)
from kdp.inspection.pdf import PDFLoadError, inspect_pdf, pdf_reader_available
from kdp.models import Artifact, ArtifactKind
from kdp.providers.mock import DEFECTS, _png
from conftest import make_metadata

pytestmark = pytest.mark.skipif(
    not pdf_reader_available(), reason="pypdf is required to inspect PDFs"
)


def codes(result) -> set[str]:
    return {f.code for f in result.findings}


# --- image inspection -------------------------------------------------------


def test_clean_line_art_has_no_problems():
    inspection = inspect_image(_png(300, 400, b"a fern"))
    assert inspection.problems() == ()
    assert inspection.midtone_fraction == 0.0
    assert inspection.border_mean == 255.0


def test_clean_line_art_measures_as_black_on_white():
    inspection = inspect_image(_png(300, 400, b"a fern"))
    assert inspection.white_fraction > 0.9
    assert 0.005 < inspection.ink_fraction < 0.60
    assert inspection.edge_ink_fraction == 0.0


@pytest.mark.parametrize(
    ("defect", "expected_code"),
    [
        ("shading", "image.shading_present"),
        ("colour", "image.colour_present"),
        ("clipped", "image.clipped_at_edge"),
        ("blank", "image.no_drawing"),
        ("solid", "image.too_much_ink"),
        ("watermark", "image.possible_mark"),
    ],
)
def test_each_defect_is_detected(defect, expected_code):
    issues = inspect_image(_png(300, 400, b"a fern", defect=defect)).problems()
    assert expected_code in {i.code for i in issues}


def test_every_defect_the_mock_can_inject_is_detected():
    """Nothing the provider can produce goes unnoticed."""
    for defect in DEFECTS:
        issues = inspect_image(_png(300, 400, b"a fern", defect=defect)).problems()
        assert issues, f"{defect} produced no findings"


def test_a_watermark_is_a_warning_not_a_verdict():
    # A corner mark is a heuristic. It must reach a human, not stop the book
    # on its own — the inspector cannot tell a signature from a dark corner.
    issues = inspect_image(_png(300, 400, b"a fern", defect="watermark")).problems()
    marks = [i for i in issues if i.code == "image.possible_mark"]
    assert marks and all(not i.blocking for i in marks)


def test_shading_just_under_the_threshold_passes():
    """The boundary is a decision, so it is tested from both sides."""
    inspection = inspect_image(_png(300, 400, b"a fern"))
    assert inspection.midtone_fraction <= MAX_MIDTONE_FRACTION
    shaded = inspect_image(_png(300, 400, b"a fern", defect="shading"))
    assert shaded.midtone_fraction > MAX_MIDTONE_FRACTION


def test_text_detection_is_reported_as_not_attempted():
    # The honest claim. "Checked, clean" would be a lie: nothing looked.
    inspection = inspect_image(_png(120, 120, b"x"))
    assert any("OCR" in note for note in inspection.not_checked)


def test_an_undecodable_file_raises_rather_than_reporting_clean():
    with pytest.raises(ImageLoadError):
        inspect_image(b"this is not an image")


def test_a_png_loads_without_the_optional_decoder(monkeypatch):
    """Capability is per-file: a PNG needs nothing installed."""
    from kdp.inspection import image as module

    monkeypatch.setattr(module, "_pillow_available", lambda: False)
    assert load_image(_png(64, 64, b"x")).width == 64


def test_effective_dpi_is_computed_against_the_placement_box():
    inspection = inspect_image(_png(2400, 3000, b"x"))
    assert inspection.effective_dpi(8.0, 10.0) == pytest.approx(300.0)
    assert inspection.effective_dpi(0, 0) == 0.0


# --- perceptual hashing -----------------------------------------------------


def test_the_same_drawing_rescaled_hashes_the_same():
    a = perceptual_hash(_png(300, 380, b"a fern"))
    b = perceptual_hash(_png(180, 228, b"a fern"))
    assert hamming(a, b) <= 10


def test_different_drawings_hash_apart():
    a = perceptual_hash(_png(300, 380, b"a fern"))
    b = perceptual_hash(_png(300, 380, b"a thistle"))
    assert hamming(a, b) > 10


def test_perceptual_hashing_is_deterministic():
    assert perceptual_hash(_png(200, 200, b"x")) == perceptual_hash(_png(200, 200, b"x"))


# --- PDF inspection ---------------------------------------------------------


@pytest.fixture
def built(built_manifest, tmp_path):
    """A manifest with a real interior and cover PDF on disk."""
    manifest, assets_dir = built_manifest
    build = tmp_path / "build"
    build.mkdir(exist_ok=True)

    interior = build / "interior.pdf"
    interior.write_bytes(build_interior(manifest, assets_dir))
    manifest = manifest.with_artifact(
        Artifact.from_file(ArtifactKind.INTERIOR_PDF, interior, created_at="2026-01-01T00:00:00+00:00")
    )
    cover = build / "cover.pdf"
    cover.write_bytes(build_cover(manifest))
    manifest = manifest.with_artifact(
        Artifact.from_file(ArtifactKind.COVER_PDF, cover, created_at="2026-01-01T01:00:00+00:00")
    )
    return manifest, assets_dir, build


def test_a_built_interior_matches_its_specification(built):
    manifest, _, _ = built
    inspection = inspect_pdf(manifest.artifact(ArtifactKind.INTERIOR_PDF).path)
    assert inspection.page_count == manifest.spec.page_count
    assert len(inspection.distinct_page_sizes) == 1
    assert check_interior(manifest, inspection) == ()


def test_a_built_cover_matches_its_geometry(built):
    manifest, _, _ = built
    inspection = inspect_pdf(manifest.artifact(ArtifactKind.COVER_PDF).path)
    assert inspection.page_count == 1
    assert check_cover(manifest, inspection) == ()


def test_interior_pages_carry_bleed(built):
    manifest, _, _ = built
    inspection = inspect_pdf(manifest.artifact(ArtifactKind.INTERIOR_PDF).path)
    width, height = inspection.distinct_page_sizes[0]
    # 8.5x11 plus bleed: +0.125 wide, +0.25 tall.
    assert width == pytest.approx(8.625, abs=0.001)
    assert height == pytest.approx(11.25, abs=0.001)


def test_artwork_lands_on_exactly_the_pages_the_spec_describes(built):
    manifest, _, _ = built
    inspection = inspect_pdf(manifest.artifact(ArtifactKind.INTERIOR_PDF).path)
    assert set(inspection.pages_with_images()) == {
        p.index for p in manifest.spec.art_pages
    }


def test_a_page_count_change_makes_the_cover_wrong(built):
    """The quiet failure: the file is intact, the spine is not."""
    from dataclasses import replace

    manifest, _, _ = built
    inspection = inspect_pdf(manifest.artifact(ArtifactKind.COVER_PDF).path)

    # The interior gained pages after the cover was built.
    grown = replace(manifest, spec=replace(manifest.spec, page_count=300))
    issues = check_cover(grown, inspection)
    codes_found = {i.code for i in issues}
    assert "cover.width_mismatch" in codes_found
    detail = next(i.detail for i in issues if i.code == "cover.width_mismatch")
    assert detail["implied_spine_in"] < detail["expected_spine_in"]


def test_an_interior_with_the_wrong_page_count_is_caught(built):
    from dataclasses import replace

    manifest, _, _ = built
    inspection = inspect_pdf(manifest.artifact(ArtifactKind.INTERIOR_PDF).path)
    claimed = replace(manifest, spec=replace(manifest.spec, page_count=100))
    assert "interior.page_count_mismatch" in {
        i.code for i in check_interior(claimed, inspection)
    }


def test_an_unprintable_page_count_reports_rather_than_raising(built):
    """A gate that throws crashes the run instead of stopping the book."""
    from dataclasses import replace

    manifest, _, _ = built
    inspection = inspect_pdf(manifest.artifact(ArtifactKind.INTERIOR_PDF).path)
    impossible = replace(manifest, spec=replace(manifest.spec, page_count=999))

    issues = check_interior(impossible, inspection)
    assert "interior.geometry_unresolvable" in {i.code for i in issues}
    # And the gate around it blocks rather than propagating an exception.
    assert interior_qa.run(impossible).status.allows_advance is False


def test_an_interior_built_without_bleed_is_caught(built_manifest, tmp_path):
    from dataclasses import replace

    manifest, assets_dir = built_manifest
    no_bleed = replace(manifest, spec=replace(manifest.spec, bleed=False))
    target = tmp_path / "no-bleed.pdf"
    target.write_bytes(build_interior(no_bleed, assets_dir))

    # Inspect the no-bleed file against the spec that asks for bleed.
    inspection = inspect_pdf(target)
    issues = check_interior(manifest, inspection)
    assert "interior.page_size_mismatch" in {i.code for i in issues}


def test_a_corrupt_pdf_blocks_rather_than_passing(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\nnot really a pdf at all\n")
    with pytest.raises(PDFLoadError):
        inspect_pdf(broken)


def test_a_missing_pdf_raises(tmp_path):
    with pytest.raises(PDFLoadError, match="no file at"):
        inspect_pdf(tmp_path / "absent.pdf")


# --- the gates, end to end over real files ----------------------------------


def test_interior_qa_passes_a_correctly_built_book(built):
    manifest, _, _ = built
    result = interior_qa.run(manifest)
    assert result.status is Status.PASS, result.to_json()


def test_cover_qa_passes_a_correctly_built_cover(built):
    manifest, _, _ = built
    assert cover_qa.run(manifest).status is Status.PASS


def test_preflight_passes_over_real_files(built):
    manifest, _, _ = built
    result = print_preflight.run(
        manifest,
        interior_pdf=manifest.artifact(ArtifactKind.INTERIOR_PDF).path,
        cover_pdf=manifest.artifact(ArtifactKind.COVER_PDF).path,
    )
    assert result.status is Status.PASS, result.to_json()


def test_preflight_blocks_on_a_corrupt_interior(built, tmp_path):
    manifest, _, build = built
    (build / "interior.pdf").write_bytes(b"%PDF-1.4\ngarbage\n")
    result = print_preflight.run(
        manifest,
        interior_pdf=build / "interior.pdf",
        cover_pdf=manifest.artifact(ArtifactKind.COVER_PDF).path,
    )
    assert result.status is Status.BLOCKED
    assert "could not be opened" in result.findings[0].message


def test_interior_qa_blocks_on_a_corrupt_pdf(built, tmp_path):
    from dataclasses import replace

    manifest, _, build = built
    target = build / "interior.pdf"
    target.write_bytes(b"%PDF-1.4\ngarbage\n")
    # Re-record the hash so the change is not caught as tampering first: the
    # point of this test is the parse failure, not the hash mismatch.
    restated = manifest.with_artifact(
        Artifact.from_file(ArtifactKind.INTERIOR_PDF, target, created_at="2026-01-01T00:00:00+00:00")
    )
    result = interior_qa.run(restated)
    assert result.status is Status.BLOCKED
    assert "could not be inspected" in result.findings[0].message


def test_asset_qa_passes_clean_pages_and_names_what_it_checked(built_manifest):
    manifest, assets_dir = built_manifest
    result = asset_qa.run(manifest, assets_dir=assets_dir)
    assert result.status is Status.PASS, result.to_json()
    assert result.evidence["pages_inspected"] == len(manifest.spec.art_pages)
    assert "pure white background" in result.evidence["pixel_checks"]


def test_asset_qa_fails_a_shaded_page(built_manifest):
    from dataclasses import replace

    from kdp.models import content_hash

    manifest, assets_dir = built_manifest
    page = manifest.spec.art_pages[0].page_id
    bad = _png(240, 240, page.encode(), defect="shading")
    (assets_dir / f"{page}.png").write_bytes(bad)
    updated = tuple(
        replace(a, content_hash=content_hash(bad)) if a.page == page else a
        for a in manifest.assets
    )

    result = asset_qa.run(manifest.with_assets(updated), assets_dir=assets_dir)
    assert result.status is Status.FAIL
    assert "image.shading_present" in codes(result)


def test_asset_qa_catches_provenance_disagreeing_with_the_file(built_manifest):
    from dataclasses import replace

    manifest, assets_dir = built_manifest
    page = manifest.spec.art_pages[0].page_id
    lying = tuple(
        replace(a, width=9999, height=9999) if a.page == page else a
        for a in manifest.assets
    )
    result = asset_qa.run(manifest.with_assets(lying), assets_dir=assets_dir)
    assert "asset_qa.dimensions_disagree" in codes(result)
