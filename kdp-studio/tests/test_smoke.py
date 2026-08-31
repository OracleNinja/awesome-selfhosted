"""The single-page smoke test.

Its purpose is to be trusted before a forty-image spend, so the properties that
matter are what it *does not* do: it buys one image, touches nothing, and
reports the same measurements the gates will apply later.

Every test drives the shared google-genai-shaped stub. Zero network calls, no
key, nothing paid for.
"""

from __future__ import annotations

import json

import pytest

from kdp.providers.google_imagen import GoogleImagenProvider
from kdp.providers.mock import MockImageProvider, _png
from kdp.smoke import SmokeTestError, run_smoke_test
from conftest import StubClient, StubGenerated, StubImage, StubResponse, stub_client_returning
from fixtures import build_manifest


@pytest.fixture
def book():
    return build_manifest()


def _imagen(data: bytes) -> GoogleImagenProvider:
    return GoogleImagenProvider(client=stub_client_returning(data))


def _page(book) -> str:
    return book.spec.art_pages[0].page_id


# --- exactly one page, and nothing else ------------------------------------


def test_it_makes_exactly_one_provider_call(book, tmp_path):
    """The whole point: one image, not forty."""
    client = stub_client_returning(_png(2520, 3360, b"x"))
    provider = GoogleImagenProvider(client=client)

    run_smoke_test(book, _page(book), provider, tmp_path)

    assert len(client.models.calls) == 1
    assert len(book.spec.art_pages) == 24  # the rest were left alone


def test_it_generates_the_page_it_was_asked_for(book, tmp_path):
    client = stub_client_returning(_png(600, 800, b"x"))
    target = book.spec.art_pages[5].page_id

    report = run_smoke_test(book, target, GoogleImagenProvider(client=client), tmp_path)

    assert report.page_id == target
    prompt = book.prompt_plan.for_page(target)
    assert client.models.calls[0]["prompt"] == prompt.render_positive()


def test_it_never_writes_to_the_manifest(book, tmp_path):
    """The promise that makes running this safe, asserted rather than assumed."""
    path = tmp_path / "manifest.json"
    book.write(path)
    before = path.read_bytes()

    run_smoke_test(book, _page(book), _imagen(_png(600, 800, b"x")), tmp_path / "smoke")

    assert path.read_bytes() == before


def test_the_returned_manifest_state_is_unchanged(book, tmp_path):
    run_smoke_test(book, _page(book), _imagen(_png(600, 800, b"x")), tmp_path)
    assert book.stage == "research"
    assert book.assets == ()


# --- it sends what the batch would send ------------------------------------


def test_it_sends_the_same_request_the_batch_would(book, tmp_path):
    """A smoke test that built its own request would prove nothing."""
    from kdp.generation import build_request

    client = stub_client_returning(_png(600, 800, b"x"))
    page = _page(book)
    run_smoke_test(book, page, GoogleImagenProvider(client=client), tmp_path)

    expected = build_request(book.prompt_plan.for_page(page), attempt=1)
    call = client.models.calls[0]
    assert call["prompt"] == expected.prompt_positive
    assert call["config"].negative_prompt == expected.prompt_negative


def test_prohibitions_still_go_in_the_negative_prompt(book, tmp_path):
    client = stub_client_returning(_png(600, 800, b"x"))
    run_smoke_test(book, _page(book), GoogleImagenProvider(client=client), tmp_path)
    call = client.models.calls[0]
    assert "Must not contain" not in call["prompt"]
    assert "colour" in call["config"].negative_prompt


# --- it runs the real inspection and the real arithmetic -------------------


def test_a_print_resolution_image_passes(book, tmp_path):
    report = run_smoke_test(book, _page(book), _imagen(_png(2520, 3360, b"x")), tmp_path)

    assert report.call_status == "ok"
    assert (report.width, report.height) == (2520, 3360)
    assert report.effective_dpi >= report.required_dpi
    assert report.resolution_ok is True
    assert report.findings == ()
    assert report.would_pass_asset_qa is True


def test_an_undersized_image_fails_on_resolution(book, tmp_path):
    """The exact risk with a model that picks its own output size."""
    report = run_smoke_test(book, _page(book), _imagen(_png(600, 800, b"x")), tmp_path)

    assert report.call_status == "ok"
    assert report.resolution_ok is False
    assert report.effective_dpi < 300
    assert report.would_pass_asset_qa is False


def test_effective_dpi_is_measured_against_the_book_s_live_area(book, tmp_path):
    from kdp.specs import get_trim, interior_geometry

    report = run_smoke_test(book, _page(book), _imagen(_png(2520, 3360, b"x")), tmp_path)
    geometry = interior_geometry(
        get_trim(book.spec.trim), book.spec.page_count, book.spec.bleed
    )
    assert report.live_width_in == geometry.live_width_in
    assert report.effective_dpi == pytest.approx(
        min(2520 / geometry.live_width_in, 3360 / geometry.live_height_in)
    )


@pytest.mark.parametrize(
    ("defect", "code"),
    [
        ("shading", "image.shading_present"),
        ("colour", "image.colour_present"),
        ("clipped", "image.clipped_at_edge"),
        ("blank", "image.no_drawing"),
    ],
)
def test_pixel_defects_are_reported_by_the_existing_inspection(
    book, tmp_path, defect, code
):
    data = _png(2520, 3360, b"x", defect=defect)
    report = run_smoke_test(book, _page(book), _imagen(data), tmp_path)

    assert code in {c for _, c, _ in report.findings}
    assert report.would_pass_asset_qa is False


def test_a_watermark_is_a_warning_not_a_blocker(book, tmp_path):
    data = _png(2520, 3360, b"x", defect="watermark")
    report = run_smoke_test(book, _page(book), _imagen(data), tmp_path)
    marks = [f for f in report.findings if f[1] == "image.possible_mark"]
    assert marks and all(f[0] == "warning" for f in marks)


# --- the artefact it leaves behind ------------------------------------------


def test_it_saves_the_image_and_a_provenance_record(book, tmp_path):
    data = _png(2520, 3360, b"x")
    report = run_smoke_test(book, _page(book), _imagen(data), tmp_path)

    from pathlib import Path

    image = Path(report.image_path)
    assert image.is_file() and image.read_bytes() == data
    assert image.parent.name == "assets"  # the normal layout

    record = json.loads(Path(report.provenance_path).read_text())
    assert record["page_id"] == _page(book)
    assert record["provider"] == "google-imagen"
    assert record["ai_role"] == "generated"
    # Pending, never approved: a smoke test does not stand in for a QA verdict.
    assert record["status"] == "pending"
    assert record["prompt_version"] == "v1"
    assert record["width"] == 2520


def test_the_provenance_record_loads_as_a_real_asset(book, tmp_path):
    """Same model as the pipeline's, so register-asset can promote it."""
    from pathlib import Path

    from kdp.models import AssetProvenance

    report = run_smoke_test(book, _page(book), _imagen(_png(600, 800, b"x")), tmp_path)
    record = AssetProvenance.from_dict(
        json.loads(Path(report.provenance_path).read_text())
    )
    assert record.page == _page(book)
    assert record.content_hash == report.content_hash


# --- failure paths ----------------------------------------------------------


def test_a_filtered_image_is_reported_and_nothing_is_written(book, tmp_path):
    client = StubClient(
        StubResponse([StubGenerated(image=None, rai_filtered_reason="blocked: policy")])
    )
    report = run_smoke_test(book, _page(book), GoogleImagenProvider(client=client), tmp_path)

    assert report.call_status == "failed"
    assert "blocked: policy" in report.call_reason
    assert report.image_path is None
    assert not (tmp_path / "assets").exists()


def test_a_transport_failure_is_reported_not_raised(book, tmp_path):
    client = StubClient(error=RuntimeError("connection reset"))
    report = run_smoke_test(book, _page(book), GoogleImagenProvider(client=client), tmp_path)

    assert report.call_status == "failed"
    assert "connection reset" in report.call_reason
    assert report.would_pass_asset_qa is False


def test_an_unknown_page_is_refused_before_any_call(book, tmp_path):
    client = stub_client_returning(_png(600, 800, b"x"))
    with pytest.raises(SmokeTestError, match="not an art page"):
        run_smoke_test(book, "PAGE-999", GoogleImagenProvider(client=client), tmp_path)
    assert client.models.calls == []


def test_a_blank_page_is_refused_before_any_call(book, tmp_path):
    """Only art pages are generated; a blank has nothing to draw."""
    blank = next(p for p in book.spec.pages if p.role == "blank")
    client = stub_client_returning(_png(600, 800, b"x"))
    with pytest.raises(SmokeTestError, match="not an art page"):
        run_smoke_test(book, blank.page_id, GoogleImagenProvider(client=client), tmp_path)
    assert client.models.calls == []


def test_a_book_with_no_prompt_plan_is_refused(book, tmp_path):
    from dataclasses import replace

    unplanned = replace(book, prompt_plan=None)
    with pytest.raises(SmokeTestError, match="no prompt plan"):
        run_smoke_test(unplanned, _page(book), _imagen(_png(600, 800, b"x")), tmp_path)


# --- the report itself ------------------------------------------------------


def test_the_report_serialises_everything_a_reviewer_needs(book, tmp_path):
    report = run_smoke_test(book, _page(book), _imagen(_png(2520, 3360, b"x")), tmp_path)
    payload = report.to_dict()

    assert payload["provider"] == "google-imagen"
    assert payload["model"]
    assert payload["image"]["mime_type"] == "image/png"
    assert payload["image"]["width"] == 2520
    assert payload["resolution"]["effective_dpi"] >= 300
    assert payload["resolution"]["required_dpi"] == 300
    assert payload["prompt"]["prompt_id"]
    assert payload["prompt"]["negative"]
    assert payload["call_status"] == "ok"
    assert payload["inspection"]["white_fraction"] > 0.9


def test_the_rendered_report_says_it_is_one_page_not_the_book(book, tmp_path):
    report = run_smoke_test(book, _page(book), _imagen(_png(2520, 3360, b"x")), tmp_path)
    text = report.render()
    assert "That is one" in text and "not the book" in text
    assert "register-asset" in text  # the promote path, not an automatic one


def test_the_report_never_carries_a_credential(book, tmp_path, monkeypatch):
    monkeypatch.setenv("KDP_GOOGLE_API_KEY", "sk-SMOKE-CANARY")
    report = run_smoke_test(book, _page(book), _imagen(_png(600, 800, b"x")), tmp_path)
    assert "SMOKE-CANARY" not in json.dumps(report.to_dict())
    assert "SMOKE-CANARY" not in report.render()


def test_it_works_with_the_mock_provider_too(book, tmp_path):
    """The interface is the point: nothing here is Imagen-specific."""
    report = run_smoke_test(book, _page(book), MockImageProvider(), tmp_path)
    assert report.call_status == "ok"
    assert report.provider == "mock"
