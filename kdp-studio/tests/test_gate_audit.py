"""Hostile inputs against every gate.

Two properties, asserted for all fourteen gates rather than one at a time:

**No exception escapes a gate.** A gate that raises stops the *run*; a gate
that returns BLOCKED stops the *book*. Only the second is a quality control.
Every crash this file covers was a real one found by running it — four of them
shared a root cause worth remembering: ``Path.exists()`` is true for a
directory, and reading one raises.

**An empty check is not a passed check.** A gate handed nothing to inspect
must block, not pass. A book with no drawings that sails through asset QA has
not been audited, and would reach a reviewer looking clean.
"""

from __future__ import annotations

import os
import stat
from dataclasses import replace

import pytest

from kdp.gates import (
    asset_qa,
    cover_qa,
    final_audit,
    human_approval,
    interior_qa,
    kdp_compliance,
    metadata_compliance,
    originality,
    print_preflight,
    prompt_plan,
    provenance_complete,
    research_hygiene,
    spec_legality,
    strategy_differentiation,
)
from kdp.models import (
    Artifact,
    ArtifactKind,
    BookManifest,
    BookSpec,
    PageSpec,
    PromptPlan,
    ResearchBrief,
    content_hash,
)
from conftest import make_metadata, make_spec, small_book_pages, write_assets

EMPTY_SPEC = BookSpec(book_id="empty", title="Empty", page_count=0)
EMPTY = BookManifest(spec=EMPTY_SPEC)


def _no_art() -> BookManifest:
    pages = tuple(PageSpec(f"P{i}", i + 1, "blank") for i in range(26))
    return BookManifest(
        spec=make_spec(pages=pages, page_count=26), metadata=make_metadata()
    )


def _bad(**overrides) -> BookManifest:
    return BookManifest(
        spec=make_spec(art_count=12, **overrides), metadata=make_metadata()
    )


# --- no exception escapes ---------------------------------------------------

DEGENERATE = {
    "G1 no brief": lambda: research_hygiene.run(None),
    "G1 empty brief": lambda: research_hygiene.run(ResearchBrief("b", "s")),
    "G7 no strategy": lambda: strategy_differentiation.run(None),
    "G2 empty spec": lambda: spec_legality.run(EMPTY_SPEC, require_verified_specs=False),
    "G2 unknown trim": lambda: spec_legality.run(
        _bad(trim="9x9").spec, require_verified_specs=False
    ),
    "G2 unknown paper": lambda: spec_legality.run(
        _bad(paper="papyrus").spec, require_verified_specs=False
    ),
    "G8 no plan": lambda: prompt_plan.run(EMPTY_SPEC, None),
    "G8 empty plan": lambda: prompt_plan.run(make_spec(art_count=4), PromptPlan("p")),
    "G3 empty": lambda: provenance_complete.run(EMPTY),
    "G4 empty": lambda: originality.run(EMPTY),
    "G4 missing dir": lambda: originality.run(_bad(), assets_dir="/nonexistent"),
    "G9 empty": lambda: asset_qa.run(EMPTY),
    "G9 no art pages": lambda: asset_qa.run(_no_art()),
    "G9 unknown trim": lambda: asset_qa.run(_bad(trim="9x9"), assets_dir="/nonexistent"),
    "G5 empty": lambda: print_preflight.run(EMPTY),
    "G5 unknown trim": lambda: print_preflight.run(_bad(trim="9x9")),
    "G5 unknown paper": lambda: print_preflight.run(_bad(paper="papyrus")),
    "G10 empty": lambda: interior_qa.run(EMPTY),
    "G11 empty": lambda: cover_qa.run(EMPTY),
    "G11 unknown trim": lambda: cover_qa.run(_bad(trim="9x9")),
    "G6 empty": lambda: metadata_compliance.run(EMPTY),
    "G12 empty": lambda: kdp_compliance.run(EMPTY, require_verified_policy=False),
    "G13 empty": lambda: final_audit.run(EMPTY, require_verified_specs=False),
    "G13 unknown trim": lambda: final_audit.run(
        _bad(trim="9x9"), require_verified_specs=False
    ),
    "G14 empty": lambda: human_approval.run(EMPTY),
}


@pytest.mark.parametrize("name", sorted(DEGENERATE))
def test_no_gate_raises_on_degenerate_input(name):
    DEGENERATE[name]()  # a raise here fails the test


@pytest.mark.parametrize("name", sorted(DEGENERATE))
def test_no_gate_passes_on_degenerate_input(name):
    """Every degenerate case must stop the book, one way or another.

    G12 is the one exception and it is a deliberate one: printability is G2's
    responsibility, and a compliance gate reaching outside its own subject
    would blur which gate owns which failure.
    """
    result = DEGENERATE[name]()
    if name.startswith("G12"):
        return
    assert result.status.allows_advance is False, result.to_json()


# --- sabotaged files --------------------------------------------------------


@pytest.fixture
def sabotage(tmp_path):
    """A real book, plus a set of things that are not the files they claim."""
    pages = small_book_pages()
    spec = make_spec(pages=pages, page_count=len(pages))
    assets = tmp_path / "assets"
    manifest = BookManifest(
        spec=spec, assets=write_assets(spec, assets), metadata=make_metadata()
    )

    good = (assets / f"{spec.art_pages[0].page_id}.png").read_bytes()
    files = {
        "directory": tmp_path / "a-directory",
        "garbage": tmp_path / "garbage.png",
        "empty": tmp_path / "empty.png",
        "truncated": tmp_path / "truncated.png",
    }
    files["directory"].mkdir()
    files["garbage"].write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    files["empty"].write_bytes(b"")
    files["truncated"].write_bytes(good[:200])
    return manifest, files, tmp_path


def _point_first_page_at(manifest: BookManifest, target) -> BookManifest:
    first = manifest.spec.art_pages[0].page_id
    return manifest.with_assets(
        tuple(
            replace(a, path=str(target)) if a.page == first else a
            for a in manifest.assets
        )
    )


@pytest.mark.parametrize("kind", ["directory", "garbage", "empty", "truncated"])
def test_asset_qa_blocks_on_an_unreadable_asset(sabotage, kind):
    manifest, files, _ = sabotage
    result = asset_qa.run(_point_first_page_at(manifest, files[kind]))
    assert result.status.allows_advance is False


@pytest.mark.parametrize("kind", ["directory", "garbage", "empty", "truncated"])
def test_originality_blocks_on_an_unreadable_asset(sabotage, kind):
    manifest, files, _ = sabotage
    result = originality.run(_point_first_page_at(manifest, files[kind]))
    assert result.status.allows_advance is False


def test_a_directory_where_a_pdf_should_be_blocks_every_pdf_gate(sabotage):
    """The bug this whole file exists for: exists() is true for a directory."""
    manifest, files, tmp_path = sabotage
    directory = files["directory"]

    fake = Artifact(ArtifactKind.INTERIOR_PDF, str(directory), content_hash(b"x"))
    fake_cover = Artifact(ArtifactKind.COVER_PDF, str(directory), content_hash(b"x"))

    assert interior_qa.run(manifest.with_artifact(fake)).status.allows_advance is False
    assert cover_qa.run(manifest.with_artifact(fake_cover)).status.allows_advance is False
    assert (
        print_preflight.run(
            manifest, interior_pdf=directory, cover_pdf=directory
        ).status.allows_advance
        is False
    )
    audited = final_audit.run(
        manifest.with_artifact(fake).with_artifact(fake_cover),
        require_verified_specs=False,
    )
    assert audited.status.allows_advance is False


def test_a_deleted_artifact_blocks_the_final_audit(sabotage):
    manifest, _, tmp_path = sabotage
    ghost = Artifact(
        ArtifactKind.INTERIOR_PDF, str(tmp_path / "gone.pdf"), content_hash(b"x")
    )
    result = final_audit.run(
        manifest.with_artifact(ghost), require_verified_specs=False
    )
    assert "final.artifact_file_missing" in {f.code for f in result.findings}


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_an_unreadable_asset_blocks_rather_than_crashing(sabotage):
    manifest, _, tmp_path = sabotage
    locked = tmp_path / "locked.png"
    locked.write_bytes((tmp_path / "assets").glob("*.png").__next__().read_bytes())
    locked.chmod(0o000)
    try:
        result = asset_qa.run(_point_first_page_at(manifest, locked))
        assert result.status.allows_advance is False
    finally:
        locked.chmod(stat.S_IRUSR | stat.S_IWUSR)


# --- stale specification handling ------------------------------------------


def test_a_stale_verification_blocks_just_like_an_unverified_one(monkeypatch):
    """A check made two years ago is not evidence about today."""
    from datetime import date

    from kdp.specs import revision

    stale = {
        name: revision.SourceRecord(
            name,
            source_url="https://kdp.amazon.com/example",
            verified_on="2020-01-01",
        )
        for name in revision.SOURCES
    }
    monkeypatch.setattr(revision, "SOURCES", stale)

    problem = revision.verification_problem(today=date(2026, 8, 30))
    assert problem is not None
    assert "more than" in problem
    assert revision.is_verified(today=date(2026, 8, 30)) is False


def test_spec_legality_requires_only_the_tables_it_uses(monkeypatch):
    """Scoping matters: an unverified royalty table says nothing about geometry."""
    from kdp.specs import revision

    sources = {
        name: revision.SourceRecord(
            name, source_url="https://kdp.amazon.com/example", verified_on="2026-08-01"
        )
        for name in revision.SOURCES
    }
    # Leave royalty and policy unverified; geometry does not depend on them.
    sources["royalty"] = revision.SourceRecord("royalty")
    sources["policy"] = revision.SourceRecord("policy")
    monkeypatch.setattr(revision, "SOURCES", sources)

    result = spec_legality.run(make_spec(art_count=12))
    assert "spec.tables_unverified" not in {f.code for f in result.findings}


def test_the_final_audit_requires_every_table(monkeypatch):
    from kdp.specs import revision

    sources = {
        name: revision.SourceRecord(
            name, source_url="https://kdp.amazon.com/example", verified_on="2026-08-01"
        )
        for name in revision.SOURCES
    }
    sources["royalty"] = revision.SourceRecord("royalty")
    monkeypatch.setattr(revision, "SOURCES", sources)

    result = final_audit.run(EMPTY)
    assert result.status.allows_advance is False
    assert "royalty" in result.findings[0].message
