"""Provenance records and the disclosure roll-up.

The roll-up is the highest-stakes logic in the package: under-disclosing risks
the listing and the account. Its fail-closed behaviour is tested from several
directions on purpose.
"""

from __future__ import annotations

import pytest

from kdp.errors import ModelError
from kdp.models import (
    AIRole,
    AssetKind,
    AssetProvenance,
    BookManifest,
    content_hash,
    hash_file,
    roll_up_disclosure,
)
from conftest import make_assets, make_metadata, make_spec


def record(asset_id: str, kind: AssetKind, role: AIRole) -> AssetProvenance:
    return AssetProvenance(
        asset_id=asset_id,
        kind=kind,
        ai_role=role,
        content_hash=content_hash(asset_id.encode()),
        tool="tool" if role in (AIRole.ASSISTED, AIRole.GENERATED) else None,
    )


def test_no_ai_means_no_disclosure():
    answer = roll_up_disclosure([record("a", AssetKind.IMAGE, AIRole.NONE)])
    assert answer.required is False
    assert answer.complete is True
    assert "not required" in answer.statement()


def test_ai_assisted_alone_does_not_require_disclosure():
    # The distinction KDP actually draws: assistance is not generation.
    answer = roll_up_disclosure([record("a", AssetKind.TEXT, AIRole.ASSISTED)])
    assert answer.required is False
    assert answer.categories == ()


def test_one_generated_asset_requires_disclosure():
    answer = roll_up_disclosure(
        [
            record("a", AssetKind.IMAGE, AIRole.GENERATED),
            record("b", AssetKind.TEXT, AIRole.NONE),
        ]
    )
    assert answer.required is True
    assert answer.categories == ("image",)
    assert answer.generated_assets == ("a",)


def test_categories_cover_every_disclosable_kind():
    answer = roll_up_disclosure(
        [
            record("a", AssetKind.IMAGE, AIRole.GENERATED),
            record("b", AssetKind.TEXT, AIRole.GENERATED),
            record("c", AssetKind.TRANSLATION, AIRole.GENERATED),
        ]
    )
    assert answer.categories == ("image", "text", "translation")


def test_non_disclosure_kinds_are_tracked_but_not_reported_as_categories():
    # Generated metadata is recorded for our own audit trail, but KDP's
    # question is about text, images and translations.
    answer = roll_up_disclosure([record("a", AssetKind.METADATA, AIRole.GENERATED)])
    assert answer.required is True
    assert answer.categories == ()


def test_unknown_role_makes_the_answer_incomplete():
    answer = roll_up_disclosure(
        [
            record("a", AssetKind.IMAGE, AIRole.GENERATED),
            record("b", AssetKind.IMAGE, AIRole.UNKNOWN),
        ]
    )
    assert answer.complete is False
    assert answer.unclassified == ("b",)
    assert "INCOMPLETE" in answer.statement()


def test_unknown_role_never_rolls_up_to_no_disclosure():
    # The dangerous failure: an unclassified asset silently reading as clean.
    answer = roll_up_disclosure([record("only", AssetKind.IMAGE, AIRole.UNKNOWN)])
    assert answer.complete is False
    assert answer.required is False
    assert "not required" not in answer.statement()


def test_empty_book_is_complete_and_undisclosed():
    answer = roll_up_disclosure([])
    assert (answer.complete, answer.required) == (True, False)


def test_ai_claim_without_a_tool_is_rejected():
    with pytest.raises(ModelError, match="names no tool"):
        AssetProvenance("a", AssetKind.IMAGE, AIRole.GENERATED, content_hash(b"x"))


def test_content_hash_must_be_an_addressed_digest():
    with pytest.raises(ModelError, match="sha256"):
        AssetProvenance("a", AssetKind.IMAGE, AIRole.NONE, "deadbeef")


def test_content_hash_is_stable_and_distinguishing():
    assert content_hash(b"same") == content_hash(b"same")
    assert content_hash(b"same") != content_hash(b"other")


def test_hash_file_matches_content_hash(tmp_path):
    target = tmp_path / "art.bin"
    target.write_bytes(b"page bytes")
    assert hash_file(target) == content_hash(b"page bytes")


def test_manifest_round_trips_through_json(tmp_path):
    spec = make_spec(page_count=10)
    original = BookManifest(
        spec=spec, assets=make_assets(spec), metadata=make_metadata(), stage="qa"
    )
    path = original.write(tmp_path / "manifest.json")
    assert BookManifest.read(path).to_json() == original.to_json()


def test_manifest_json_is_canonical(tmp_path):
    spec = make_spec(page_count=10)
    manifest = BookManifest(spec=spec, assets=make_assets(spec))
    # Two serialisations of the same book are byte-identical, so a diff on a
    # stored manifest always means something really changed.
    assert manifest.to_json() == manifest.to_json()
    assert manifest.to_json().endswith("\n")


def test_hand_edited_disclosure_block_cannot_lie_to_a_gate(tmp_path):
    spec = make_spec(page_count=10)
    manifest = BookManifest(
        spec=spec, assets=make_assets(spec, ai_role=AIRole.UNKNOWN)
    )
    payload = manifest.to_dict()
    payload["disclosure"] = {
        "required": False,
        "categories": [],
        "complete": True,
        "unclassified": [],
        "generated_assets": [],
    }
    # from_dict re-derives the answer from the assets rather than trusting the
    # serialised block, so tampering with the file changes nothing.
    assert BookManifest.from_dict(payload).disclosure().complete is False
