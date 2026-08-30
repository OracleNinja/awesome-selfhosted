"""The command line contract.

Exit codes are what CI and the orchestrating agent branch on, so they are the
thing under test: 0 passed, 1 stopped, 2 misused.
"""

from __future__ import annotations

import json

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
    assert "UNVERIFIED" in capsys.readouterr().err


def test_research_passes_a_clean_brief(tmp_path, brief, capsys):
    path = write(tmp_path / "brief.json", brief.to_dict())
    assert main(["research", path]) == EXIT_OK
    assert "PASS" in capsys.readouterr().out


def test_research_stops_on_a_quoted_observation(tmp_path, brief):
    payload = brief.to_dict()
    payload["signals"][0]["observations"] = ['"copied blurb"']
    assert main(["research", write(tmp_path / "b.json", payload)]) == EXIT_STOP


def test_concept_stops_while_spec_tables_are_unverified(tmp_path, manifest):
    path = str(manifest.write(tmp_path / "m.json"))
    assert main(["concept", path]) == EXIT_STOP


def test_concept_passes_when_unverified_tables_are_explicitly_allowed(tmp_path, manifest):
    path = str(manifest.write(tmp_path / "m.json"))
    assert main(["concept", path, "--allow-unverified-specs"]) == EXIT_OK


def test_generation_stops_on_unclassified_assets(tmp_path, manifest):
    unclassified = manifest.with_assets(make_assets(manifest.spec, ai_role=AIRole.UNKNOWN))
    path = str(unclassified.write(tmp_path / "m.json"))
    assert main(["generation", path]) == EXIT_STOP


def test_qa_exact_match_only_passes_a_clean_book(tmp_path, manifest):
    path = str(manifest.write(tmp_path / "m.json"))
    assert main(["qa", path, "--exact-match-only"]) == EXIT_OK


def test_qa_stops_on_a_catalogue_duplicate(tmp_path, manifest):
    path = str(manifest.write(tmp_path / "m.json"))
    catalogue = write(
        tmp_path / "cat.json", {manifest.assets[0].content_hash: "book-000"}
    )
    assert main(["qa", path, "--catalogue", catalogue, "--exact-match-only"]) == EXIT_STOP


def test_production_blocks_without_pdfs(tmp_path, manifest):
    path = str(manifest.write(tmp_path / "m.json"))
    assert main(["production", path]) == EXIT_STOP


def test_publishing_prep_passes_complete_metadata(tmp_path, manifest):
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

    assert BookManifest.read(manifest_path).stage == "concept"


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
