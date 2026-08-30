"""The whole pipeline, on a fixture book, with a mock provider.

Walks research to publication package and asserts the properties that make the
system trustworthy rather than merely functional: gates stop the book, a
generation failure is visible, approval is invalidated by a later edit, and
nothing publishes.

Runs offline and costs nothing. The mock provider is deterministic, so the
whole thing is reproducible.
"""

from __future__ import annotations

import zipfile

import pytest

from kdp.assembly import build_cover, build_interior
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
from kdp.gates.base import Status
from kdp.gates.runner import run_stage
from kdp.generation import approve_asset, generate_book
from kdp.models import Artifact, ArtifactKind, ApprovalRecord, ApprovalStatus
from kdp.package import PackagingError, build_package
from kdp.pipeline import STAGES, advance
from kdp.providers import MockImageProvider
from kdp.review import build_review
from fixtures import build_manifest

# The spec tables are unverified by design, and staying blocked on them is
# itself tested (test_gates / test_specs). The end-to-end walk opts past them
# so it can exercise everything downstream.
UNVERIFIED_OK = {"require_verified_specs": False}


@pytest.fixture
def book(tmp_path):
    """A generated, approved book on disk, plus its directory."""
    manifest = build_manifest()
    provider = MockImageProvider()

    outcome = generate_book(manifest, provider, tmp_path / "assets", now="2026-01-01T00:00:00+00:00")
    assert outcome.ok, outcome.failed
    manifest = outcome.manifest

    for page in manifest.spec.art_pages:
        latest = manifest.attempts(page.page_id)[-1]
        manifest = approve_asset(manifest, latest.asset_id)

    (tmp_path / "build").mkdir(exist_ok=True)
    interior = tmp_path / "build" / "interior.pdf"
    interior.write_bytes(build_interior(manifest, tmp_path / "assets"))
    manifest = manifest.with_artifact(
        Artifact.from_file(ArtifactKind.INTERIOR_PDF, interior, created_at="2026-01-01T01:00:00+00:00")
    )

    cover = tmp_path / "build" / "cover.pdf"
    cover.write_bytes(build_cover(manifest))
    manifest = manifest.with_artifact(
        Artifact.from_file(ArtifactKind.COVER_PDF, cover, created_at="2026-01-01T02:00:00+00:00")
    )

    from dataclasses import replace

    manifest = replace(
        manifest,
        economics=replace(manifest.economics, selected="$7.99"),
    )
    return manifest, tmp_path


# --- the walk ---------------------------------------------------------------


def test_every_stage_gate_passes_in_order(book):
    """Research through approval, one stage at a time, no skipping."""
    manifest, root = book
    manifest = manifest.with_stage("research")

    reports = {
        "research": [research_hygiene.run(manifest.research)],
        "strategy": [strategy_differentiation.run(manifest.strategy)],
        "concept": [spec_legality.run(manifest.spec, **UNVERIFIED_OK)],
        "planning": [prompt_plan.run(manifest.spec, manifest.prompt_plan)],
        "generation": [provenance_complete.run(manifest)],
        "qa": [
            originality.run(manifest, assets_dir=root / "assets"),
            asset_qa.run(manifest, assets_dir=root / "assets"),
        ],
        "production": [
            print_preflight.run(
                manifest,
                interior_pdf=manifest.artifact(ArtifactKind.INTERIOR_PDF).path,
                cover_pdf=manifest.artifact(ArtifactKind.COVER_PDF).path,
            )
        ],
        "production_qa": [interior_qa.run(manifest), cover_qa.run(manifest)],
        "publishing_prep": [
            metadata_compliance.run(manifest),
            kdp_compliance.run(manifest, require_verified_policy=False),
        ],
    }

    for stage, results in reports.items():
        report = run_stage(stage, results)
        assert report.passed, f"{stage} did not pass:\n{report.summary()}"
        manifest = advance(manifest, report)

    assert manifest.stage == "final_audit"

    audit = final_audit.run(manifest, **UNVERIFIED_OK)
    assert audit.status is Status.PASS, audit.to_json()
    assert final_audit.verdict(audit) == "FINAL_PASS"
    manifest = advance(manifest, run_stage("final_audit", [audit]))
    assert manifest.stage == "human_approval"

    # G14 blocks until a person decides. No flag gets past it.
    assert human_approval.run(manifest).status is Status.BLOCKED

    from dataclasses import replace

    approved = replace(
        manifest,
        approval=ApprovalRecord(
            status=ApprovalStatus.APPROVED,
            reviewer="a.reviewer",
            decided_at="2026-01-02T00:00:00+00:00",
            book_fingerprint=manifest.production_fingerprint(),
        ),
    )
    gate = human_approval.run(approved)
    assert gate.status is Status.PASS
    final = advance(approved, run_stage("human_approval", [gate]))
    assert final.stage == STAGES[-1] == "ready_for_submission"


def test_publication_package_contains_everything_a_human_needs(book, tmp_path):
    manifest, root = book
    from dataclasses import replace

    manifest = replace(
        manifest,
        approval=ApprovalRecord(
            status=ApprovalStatus.APPROVED,
            reviewer="a.reviewer",
            decided_at="2026-01-02T00:00:00+00:00",
            book_fingerprint=manifest.production_fingerprint(),
        ),
    )
    result = build_package(manifest, root / "build" / "publication.zip")

    with zipfile.ZipFile(result.path) as archive:
        names = set(archive.namelist())
        checklist = archive.read("SUBMISSION_CHECKLIST.md").decode()
        disclosure = archive.read("ai_disclosure.json").decode()

    assert names == {
        "interior_pdf.pdf",
        "cover_pdf.pdf",
        "manifest.json",
        "review.md",
        "gate_results.json",
        "ai_disclosure.json",
        "approval.json",
        "listing.json",
        "SUBMISSION_CHECKLIST.md",
    }
    assert "Submission is manual" in checklist
    assert "Disclose to KDP for: image" in disclosure


# --- the properties that matter ---------------------------------------------


def test_package_refuses_without_approval(book, tmp_path):
    manifest, root = book
    with pytest.raises(PackagingError, match="approval status is 'pending'"):
        build_package(manifest, root / "build" / "publication.zip")


def test_editing_the_book_invalidates_an_existing_approval(book):
    """The property the fingerprint exists for."""
    manifest, _ = book
    from dataclasses import replace

    approved = replace(
        manifest,
        approval=ApprovalRecord(
            status=ApprovalStatus.APPROVED,
            reviewer="a.reviewer",
            decided_at="2026-01-02T00:00:00+00:00",
            book_fingerprint=manifest.production_fingerprint(),
        ),
    )
    assert approved.approval_is_current()
    assert human_approval.run(approved).status is Status.PASS

    # Change the listing after sign-off.
    edited = replace(
        approved,
        metadata=replace(approved.metadata, title="Quiet Gardens, Revised"),
    )
    assert edited.approval_is_current() is False

    gate = human_approval.run(edited)
    assert gate.status is Status.FAIL
    assert "approval.stale" in {f.code for f in gate.findings}

    with pytest.raises(PackagingError, match="changed since it was approved"):
        build_package(edited, "/tmp/should-not-exist.zip")


def test_a_generation_failure_is_recorded_and_never_substituted(tmp_path):
    manifest = build_manifest()
    doomed = manifest.spec.art_pages[3].page_id
    provider = MockImageProvider(fail_pages=(doomed,))

    outcome = generate_book(
        manifest, provider, tmp_path / "assets", max_attempts=2,
        now="2026-01-01T00:00:00+00:00",
    )

    assert outcome.ok is False
    assert doomed in outcome.failed

    result = outcome.manifest
    attempts = result.attempts(doomed)
    assert len(attempts) == 2
    assert all(a.status.value == "failed" for a in attempts)
    assert all(a.failure_reason for a in attempts)
    assert result.approved_for(doomed) is None

    # No file was written for the failed page, and no neighbour was reused.
    written = {p.name for p in (tmp_path / "assets").glob("*.png")}
    assert not any(name.startswith(doomed) for name in written)

    gate = asset_qa.run(result, assets_dir=tmp_path / "assets")
    assert gate.status.allows_advance is False
    assert "asset_qa.no_approved_version" in {f.code for f in gate.findings}


def test_a_repaired_page_supersedes_the_failure_and_ships(tmp_path):
    manifest = build_manifest()
    doomed = manifest.spec.art_pages[3].page_id

    failing = generate_book(
        manifest, MockImageProvider(fail_pages=(doomed,)), tmp_path / "assets",
        max_attempts=1, now="2026-01-01T00:00:00+00:00",
    ).manifest

    # Re-run with a working provider: only the unfinished page is regenerated.
    for page in failing.spec.art_pages:
        if page.page_id == doomed:
            continue
        latest = failing.attempts(page.page_id)[-1]
        failing = approve_asset(failing, latest.asset_id)

    repaired = generate_book(
        failing, MockImageProvider(), tmp_path / "assets",
        now="2026-01-01T03:00:00+00:00",
    )

    assert repaired.ok
    assert repaired.generated == (doomed,)
    assert len(repaired.skipped) == len(manifest.spec.art_pages) - 1

    final = approve_asset(
        repaired.manifest, repaired.manifest.attempts(doomed)[-1].asset_id
    )
    assert final.approved_for(doomed) is not None
    # The failed attempt is still in the history.
    assert any(a.status.value == "failed" for a in final.attempts(doomed))


def test_approved_pages_are_not_regenerated(tmp_path):
    """A metered provider must not be asked for work already signed off."""
    manifest = build_manifest()
    first = generate_book(
        manifest, MockImageProvider(), tmp_path / "assets",
        now="2026-01-01T00:00:00+00:00",
    ).manifest
    for page in first.spec.art_pages:
        first = approve_asset(first, first.attempts(page.page_id)[-1].asset_id)

    again = generate_book(
        first, MockImageProvider(), tmp_path / "assets",
        now="2026-01-01T04:00:00+00:00",
    )
    assert again.generated == ()
    assert len(again.skipped) == len(manifest.spec.art_pages)


def test_the_build_is_deterministic(book, tmp_path):
    """Same book, same bytes — which is what makes the fingerprint mean something."""
    manifest, root = book
    first = build_interior(manifest, root / "assets")
    second = build_interior(manifest, root / "assets")
    assert first == second
    assert build_cover(manifest) == build_cover(manifest)


def test_disclosure_counts_only_what_ships(book):
    """A rejected AI attempt is not in the book and must not be disclosed as if it were."""
    manifest, _ = book
    from kdp.generation import reject_asset

    page = manifest.spec.art_pages[0].page_id
    approved = manifest.approved_for(page)
    assert approved is not None

    rejected = reject_asset(manifest, approved.asset_id, "stray shading")
    answer = rejected.disclosure()
    assert approved.asset_id not in answer.generated_assets
    assert len(answer.generated_assets) == len(manifest.spec.art_pages) - 1


def test_review_package_states_what_was_not_checked(book):
    """A blocked gate must reach the reviewer as a gap, not as silence."""
    manifest, _ = book
    # Point the gate at a directory with nothing in it: the pages become
    # uninspectable, which is exactly the situation a reviewer must be told
    # about rather than left to infer.
    blocked = asset_qa.run(manifest, assets_dir="/nonexistent")
    assert blocked.status is Status.BLOCKED

    text = build_review(manifest, [blocked])
    assert "What was NOT checked" in text
    assert asset_qa.GATE_ID in text


def test_review_package_reports_the_final_verdict(book):
    manifest, _ = book
    audit = final_audit.run(manifest, **UNVERIFIED_OK)
    text = build_review(manifest, [audit])
    assert "FINAL_PASS" in text
    assert "does not publish anything" in text
    assert "projections" in text


def test_nothing_in_the_package_publishes():
    """No module may acquire an upload path by accident.

    Checks for an HTTP client rather than for the word "amazon": the docs and
    docstrings talk about KDP constantly, and a guard that trips on prose gets
    deleted the first time it cries wolf. What matters is that nothing outside
    the provider layer can make a request at all.
    """
    import ast
    import pathlib

    import kdp

    root = pathlib.Path(kdp.__file__).parent
    clients = {"requests", "httpx", "urllib", "http", "aiohttp", "socket", "ftplib"}

    offenders = []
    for source in sorted(root.rglob("*.py")):
        if source.parent.name == "providers":
            continue  # the one layer allowed to reach a network, by design
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if name in clients:
                    offenders.append(f"{source.relative_to(root)} imports {name}")

    assert not offenders, "network access outside the provider layer: " + "; ".join(
        offenders
    )
