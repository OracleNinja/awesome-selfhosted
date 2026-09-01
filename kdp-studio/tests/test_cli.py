"""The command line contract.

Exit codes are what CI and the orchestrating agent branch on, so they are the
thing under test: 0 passed, 1 stopped, 2 misused.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kdp.cli import EXIT_OK, EXIT_STOP, EXIT_USAGE, main
from kdp.models import AIRole
from conftest import make_assets


def write(path, payload) -> str:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_specs_prints_geometry(capsys):
    assert main(["specs", "--trim", "6x9", "--pages", "120"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["interior"]["page_width_in"] == 6.125
    assert payload["cover"]["spine_text_allowed"] is True


def test_specs_warns_that_the_tables_are_unverified(capsys):
    main(["specs"])
    assert "never verified" in capsys.readouterr().err


def test_research_passes_a_clean_brief(tmp_path, brief, capsys):
    path = write(tmp_path / "brief.json", brief.to_dict())
    assert main(["research", path]) == EXIT_OK
    assert "PASS" in capsys.readouterr().out


def test_research_stops_on_a_quoted_observation(tmp_path, brief):
    payload = brief.to_dict()
    payload["signals"][0]["observations"] = ['"copied blurb"']
    assert main(["research", write(tmp_path / "b.json", payload)]) == EXIT_STOP


def test_concept_passes_now_that_the_geometry_tables_are_verified(tmp_path, manifest):
    """G2 blocked on unverified tables for several revisions.

    Trim, paper, interior and cover were verified against KDP on 2026-08-31, so
    it no longer needs the override.
    """
    path = str(manifest.write(tmp_path / "m.json"))
    assert main(["concept", path]) == EXIT_OK


def test_concept_still_accepts_the_override(tmp_path, manifest):
    path = str(manifest.write(tmp_path / "m.json"))
    assert main(["concept", path, "--allow-unverified-specs"]) == EXIT_OK


def test_generation_stops_on_unclassified_assets(tmp_path, manifest):
    unclassified = manifest.with_assets(make_assets(manifest.spec, ai_role=AIRole.UNKNOWN))
    path = str(unclassified.write(tmp_path / "m.json"))
    assert main(["generation", path]) == EXIT_STOP


def test_qa_blocks_when_the_assets_are_not_on_disk(tmp_path, manifest):
    # The manifest describes pages whose files were never written.
    path = str(manifest.write(tmp_path / "m.json"))
    assert main(["qa", path]) == EXIT_STOP


def test_qa_stops_on_a_catalogue_duplicate(tmp_path, manifest):
    path = str(manifest.write(tmp_path / "m.json"))
    catalogue = write(
        tmp_path / "cat.json", {manifest.assets[0].content_hash: "book-000"}
    )
    assert main(["qa", path, "--catalogue", catalogue]) == EXIT_STOP


def test_production_blocks_without_pdfs(tmp_path, manifest):
    path = str(manifest.write(tmp_path / "m.json"))
    assert main(["production", path]) == EXIT_STOP


def test_publishing_prep_passes_now_that_policy_is_verified(tmp_path, manifest):
    """G12 refused to certify compliance against unchecked policy data.

    The policy table was verified on 2026-08-31, so the override is no longer
    needed to get a clean listing through.
    """
    path = str(manifest.write(tmp_path / "m.json"))
    assert main(["publishing-prep", path]) == EXIT_OK


def test_disclosure_reports_the_answer(tmp_path, manifest, capsys):
    path = str(manifest.write(tmp_path / "m.json"))
    assert main(["disclosure", path]) == EXIT_OK
    assert "Disclose to KDP for: image" in capsys.readouterr().out


def test_disclosure_exits_nonzero_when_incomplete(tmp_path, manifest, capsys):
    unclassified = manifest.with_assets(make_assets(manifest.spec, ai_role=AIRole.UNKNOWN))
    path = str(unclassified.write(tmp_path / "m.json"))
    assert main(["disclosure", path]) == EXIT_STOP
    assert "INCOMPLETE" in capsys.readouterr().out


def test_advance_moves_a_manifest_on_a_passing_report(tmp_path, manifest):
    at_research = manifest.with_stage("research")
    manifest_path = str(at_research.write(tmp_path / "m.json"))
    report = write(
        tmp_path / "r.json",
        {
            "stage": "research",
            "passed": True,
            "results": [
                {"gate_id": "G1", "stage": "research", "status": "pass"}
            ],
        },
    )
    assert main(["advance", manifest_path, report]) == EXIT_OK
    from kdp.models import BookManifest

    assert BookManifest.read(manifest_path).stage == "strategy"


def test_advance_refuses_a_report_from_another_stage(tmp_path, manifest):
    manifest_path = str(manifest.with_stage("research").write(tmp_path / "m.json"))
    report = write(
        tmp_path / "r.json", {"stage": "qa", "passed": True, "results": []}
    )
    assert main(["advance", manifest_path, report]) == EXIT_STOP


def test_advance_refuses_a_failing_report(tmp_path, manifest):
    manifest_path = str(manifest.with_stage("research").write(tmp_path / "m.json"))
    report = write(
        tmp_path / "r.json", {"stage": "research", "passed": False, "results": []}
    )
    assert main(["advance", manifest_path, report]) == EXIT_STOP


def test_unreadable_input_is_a_usage_error(tmp_path):
    assert main(["research", str(tmp_path / "nope.json")]) == EXIT_USAGE


def test_malformed_json_is_a_usage_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert main(["research", str(path)]) == EXIT_USAGE


def test_stages_lists_the_pipeline(capsys):
    assert main(["stages"]) == EXIT_OK
    assert "publishing_prep" in capsys.readouterr().out


def test_json_flag_emits_machine_readable_output(tmp_path, manifest, capsys):
    path = str(manifest.write(tmp_path / "m.json"))
    main(["--json", "publishing-prep", path])
    payload = json.loads(capsys.readouterr().out)
    assert payload["stage"] == "publishing_prep"
    assert payload["passed"] is True


# --- registering an externally produced image -------------------------------


def test_register_asset_brings_an_mcp_image_into_the_pipeline(tmp_path, manifest):
    """The path for a generator this package does not drive itself.

    An agent with an image-generation MCP tool attached calls it, writes the
    file, and registers it here. The credential stays in that session and never
    reaches the manifest.
    """
    from kdp.models import BookManifest
    from kdp.providers.mock import _png

    book = tmp_path / "book"
    book.mkdir()
    path = str(manifest.write(book / "manifest.json"))
    page = manifest.spec.art_pages[0].page_id

    image = tmp_path / "from-mcp.png"
    image.write_bytes(_png(600, 780, b"a fern"))

    assert main([
        "register-asset", path, page, str(image),
        "--provider", "higgsfield", "--model", "some-model-v1",
    ]) == EXIT_OK

    updated = BookManifest.read(path)
    record = updated.attempts(page)[-1]
    assert record.provider == "higgsfield"
    assert record.tool == "some-model-v1"
    assert record.width == 600 and record.height == 780
    # Pending, not approved: an outside asset does not skip the QA verdict.
    assert record.status.value == "pending"
    # And it defaults to disclosable, which is the safe direction.
    assert record.ai_role.value == "generated"
    assert (book / "assets" / f"{record.asset_id}.png").exists()


def test_register_asset_refuses_an_unreadable_image(tmp_path, manifest):
    book = tmp_path / "book"
    book.mkdir()
    path = str(manifest.write(book / "manifest.json"))
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")

    assert main([
        "register-asset", path, manifest.spec.art_pages[0].page_id, str(broken),
        "--provider", "higgsfield", "--model", "m",
    ]) == EXIT_USAGE


def test_register_asset_requires_a_model_for_an_ai_claim(tmp_path, manifest):
    from kdp.providers.mock import _png

    book = tmp_path / "book"
    book.mkdir()
    path = str(manifest.write(book / "manifest.json"))
    image = tmp_path / "x.png"
    image.write_bytes(_png(400, 500, b"x"))

    assert main([
        "register-asset", path, manifest.spec.art_pages[0].page_id, str(image),
        "--provider", "higgsfield",
    ]) == EXIT_USAGE


def test_register_asset_rejects_a_page_the_book_does_not_have(tmp_path, manifest):
    from kdp.providers.mock import _png

    book = tmp_path / "book"
    book.mkdir()
    path = str(manifest.write(book / "manifest.json"))
    image = tmp_path / "x.png"
    image.write_bytes(_png(400, 500, b"x"))

    assert main([
        "register-asset", path, "PAGE-999", str(image),
        "--provider", "higgsfield", "--model", "m",
    ]) == EXIT_USAGE


def test_providers_reports_higgsfield_as_unavailable_and_unpriced(capsys):
    """Its cost is UNKNOWN, which the router must treat as paid."""
    assert main(["providers"]) == EXIT_OK
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if line.startswith("higgsfield"))
    assert "UNKNOWN" in row
    assert "NO" in row
    assert "mock" in out and "TEST" in out


# --- the single-page smoke test ---------------------------------------------


def _planned_book(tmp_path):
    """A book with a prompt plan, written to disk."""
    from fixtures import build_manifest

    book = tmp_path / "book"
    book.mkdir()
    manifest = build_manifest()
    return manifest, str(manifest.write(book / "manifest.json"))


def test_auto_never_spends_even_with_every_key_set(tmp_path, monkeypatch, capsys):
    """The one guarantee --auto makes, attacked with a full environment."""
    for name in ("KDP_GOOGLE_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.setenv(name, "sk-CANARY-NOT-REAL")
    monkeypatch.delenv("KDP_VECTOR_ART_DIR", raising=False)
    _, path = _planned_book(tmp_path)

    assert main(["generate", path, "--auto"]) == EXIT_STOP

    err = capsys.readouterr().err
    assert "CANARY" not in err
    assert "unavailable" in err


def test_auto_refuses_confirm_spend_rather_than_quietly_ignoring_it(tmp_path, capsys):
    _, path = _planned_book(tmp_path)
    assert main(["generate", path, "--auto", "--confirm-spend"]) == EXIT_STOP
    assert "nothing to authorise" in capsys.readouterr().err


def test_a_key_in_the_environment_does_not_authorise_a_metered_run(tmp_path, monkeypatch, capsys):
    """Having a credential is not the same as agreeing to use it."""
    monkeypatch.setenv("KDP_GOOGLE_API_KEY", "sk-CANARY-NOT-REAL")
    _, path = _planned_book(tmp_path)

    assert main(["generate", path, "--provider", "google-gemini"]) == EXIT_STOP
    assert "metered" in capsys.readouterr().err


def test_the_providers_table_names_cost_and_priority(capsys):
    assert main(["providers"]) == 0
    out = capsys.readouterr().out
    assert "Cost" in out and "Priority" in out
    assert "FREE" in out and "METERED" in out and "UNKNOWN" in out and "TEST" in out
    assert "local-vector" in out


def test_the_providers_table_says_what_auto_would_do(capsys):
    main(["providers"])
    out = capsys.readouterr().out
    assert "`--auto`" in out


def test_smoke_test_refuses_the_gemini_provider_without_confirm_spend(tmp_path):
    """The new provider is metered too. Nothing about 4K makes it free."""
    manifest, path = _planned_book(tmp_path)
    page = manifest.spec.art_pages[0].page_id
    assert main(["smoke-test", path, page, "--provider", "google-gemini"]) == EXIT_STOP


def test_generate_refuses_the_gemini_provider_without_confirm_spend(tmp_path, capsys):
    manifest, path = _planned_book(tmp_path)
    assert main(["generate", path, "--provider", "google-gemini"]) == EXIT_STOP
    assert "metered" in capsys.readouterr().err


def test_providers_says_why_imagen_cannot_be_used(capsys):
    assert main(["providers"]) == 0
    out = capsys.readouterr().out
    assert "google-gemini" in out
    assert "google-imagen" in out


def test_smoke_test_refuses_a_metered_provider_without_confirm_spend(tmp_path):
    manifest, path = _planned_book(tmp_path)
    page = manifest.spec.art_pages[0].page_id
    assert main(["smoke-test", path, page, "--provider", "google-imagen"]) == EXIT_STOP


def test_smoke_test_refuses_a_metered_provider_without_a_credential(
    tmp_path, monkeypatch, capsys
):
    from kdp.providers.google_gemini import CREDENTIAL_ENV_VAR

    monkeypatch.delenv(CREDENTIAL_ENV_VAR, raising=False)
    manifest, path = _planned_book(tmp_path)
    page = manifest.spec.art_pages[0].page_id

    assert main([
        "smoke-test", path, page, "--provider", "google-gemini", "--confirm-spend",
    ]) == EXIT_STOP

    # The property is that a metered provider which cannot run stops the command
    # and says why. Which reason comes back depends on the environment: with the
    # generation extra installed the missing key is the blocker, and in the
    # stdlib-only CI job the missing SDK is reached first. Asserting only the
    # key wording made that job fail for being configured exactly as intended.
    from kdp.providers.google_gemini import sdk_available

    stderr = capsys.readouterr().err
    assert "unavailable" in stderr
    assert ("No API key" if sdk_available() else "SDK is not installed") in stderr


def test_smoke_test_runs_one_page_and_leaves_the_manifest_alone(tmp_path, capsys):
    manifest, path = _planned_book(tmp_path)
    page = manifest.spec.art_pages[0].page_id
    before = Path(path).read_bytes()

    assert main(["smoke-test", path, page, "--provider", "mock"]) == EXIT_OK

    assert Path(path).read_bytes() == before
    out = capsys.readouterr().out
    assert page in out and "mock" in out and "DPI" in out


def test_smoke_test_exits_nonzero_when_the_page_would_fail_qa(tmp_path, monkeypatch):
    """A page that would not clear QA must not read as a green light."""
    import kdp.providers as providers
    from kdp.providers.mock import MockImageProvider

    manifest, path = _planned_book(tmp_path)
    page = manifest.spec.art_pages[0].page_id
    # Force an undersized image: the exact risk with a model that picks its
    # own output size.
    monkeypatch.setitem(
        providers.PROVIDERS, "mock", lambda: MockImageProvider(max_dimension=64)
    )
    assert main(["smoke-test", path, page, "--provider", "mock"]) == EXIT_STOP


def test_smoke_test_json_carries_the_full_report(tmp_path, capsys):
    manifest, path = _planned_book(tmp_path)
    page = manifest.spec.art_pages[0].page_id

    main(["--json", "smoke-test", path, page, "--provider", "mock"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["page_id"] == page
    assert payload["resolution"]["required_dpi"] == 300
    assert payload["prompt"]["negative"]
    assert payload["image"]["content_hash"].startswith("sha256:")


def test_smoke_test_rejects_a_page_the_book_does_not_have(tmp_path):
    _, path = _planned_book(tmp_path)
    assert main(["smoke-test", path, "PAGE-999", "--provider", "mock"]) == EXIT_USAGE
