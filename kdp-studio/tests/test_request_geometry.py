"""What size and shape a page is actually requested at.

This exists because of a real, paid failure: a page planned at 2520x3360 came
back 768x768. The request had named no output size, and the aspect ratio it did
name was one the API does not recognise — and an unrecognised ratio is not
rejected, it is ignored. The model returned its default square, it was billed
for, and the page failed the 300 DPI floor at roughly 73 DPI.

So the rules below are asserted rather than assumed:

* the ratio is derived from the pixels, and a plan that states a different one
  stops the run before it spends anything;
* an output size is always named, because the default is smaller than any print
  page needs;
* the batch and the smoke test build one request, not two.

Nothing here touches the network. The provider tests drive the SDK-shaped stub
from ``conftest``; the rest is pure arithmetic.
"""

from __future__ import annotations

import pytest

from kdp.generation import (
    NAMED_OUTPUT_SIZES,
    SUPPORTED_ASPECT_RATIOS,
    build_request,
    named_output_size,
    supported_aspect_ratio,
)
from kdp.models.prompts import AssetPrompt
from kdp.providers.google_imagen import GoogleImagenProvider, sdk_available
from kdp.providers.mock import _png
from conftest import stub_client_returning


def _prompt(**overrides) -> AssetPrompt:
    fields = dict(
        prompt_id="PROMPT-PAGE-004",
        page_id="PAGE-004",
        version="v1",
        subject="a single wide-petalled sunflower head",
        composition="one subject, centred",
        audience="adults",
        line_art_requirements="uniform black stroke, every contour closed",
        background_requirements="pure white",
        complexity=1,
        consistency_requirements="matches the other pages",
        prohibited=("colour", "shading"),
        # The real book: 8.5x11, no bleed, a 7.875 x 10.5 in live area at 300 DPI.
        width=2520,
        height=3360,
        aspect_ratio="3:4",
    )
    fields.update(overrides)
    return AssetPrompt(**fields)


# --- the ratio ---------------------------------------------------------------


def test_the_book_page_asks_for_a_ratio_the_api_accepts():
    request = build_request(_prompt())
    assert request.options["aspect_ratio"] == "3:4"
    assert request.options["aspect_ratio"] in dict(SUPPORTED_ASPECT_RATIOS)


def test_an_unsupported_ratio_stops_the_run_instead_of_being_sent():
    """"4:5" is not in either documented set. Sent, it would be ignored."""
    with pytest.raises(ValueError) as caught:
        build_request(_prompt(aspect_ratio="4:5"))
    assert "PAGE-004" in str(caught.value)
    assert "4:5" in str(caught.value)


def test_a_stated_ratio_that_contradicts_the_pixels_stops_the_run():
    """Landscape label, portrait page. One of the two is a planning bug."""
    with pytest.raises(ValueError):
        build_request(_prompt(aspect_ratio="4:3"))


def test_a_ratio_pinned_in_generation_constraints_is_checked_too():
    with pytest.raises(ValueError):
        build_request(_prompt(generation_constraints={"aspect_ratio": "16:9"}))


def test_a_page_no_supported_ratio_fits_is_refused_not_squared():
    # A deliberately odd shape: nothing in the accepted set is within tolerance.
    with pytest.raises(ValueError) as caught:
        supported_aspect_ratio(2000, 3000)
    assert "outside tolerance" in str(caught.value)


def test_the_live_area_with_bleed_still_lands_on_three_by_four():
    """7.75 x 10.25 in is 0.7561, not 0.75 — inside the 2% tolerance."""
    assert supported_aspect_ratio(2480, 3280) == "3:4"


def test_a_zero_sized_page_is_refused():
    with pytest.raises(ValueError):
        supported_aspect_ratio(0, 3360)


# --- the size ----------------------------------------------------------------


def test_the_book_page_names_the_largest_bucket_it_needs():
    """3360 px on the long edge clears 2K, so 4K is the smallest that covers it."""
    assert build_request(_prompt()).options["image_size"] == "4K"


@pytest.mark.parametrize(
    "longest, expected",
    [(800, "1K"), (1024, "1K"), (1025, "2K"), (2048, "2K"), (2049, "4K"), (3360, "4K")],
)
def test_the_smallest_bucket_that_covers_the_long_edge_is_chosen(longest, expected):
    assert named_output_size(longest)[0] == expected


def test_a_page_bigger_than_any_bucket_asks_for_the_biggest_available():
    """It does not lower the requirement — the shortfall surfaces as DPI."""
    label, pixels = named_output_size(99_999)
    assert (label, pixels) == NAMED_OUTPUT_SIZES[-1]


def test_a_size_pinned_in_generation_constraints_wins():
    request = build_request(_prompt(generation_constraints={"image_size": "2K"}))
    assert request.options["image_size"] == "2K"


def test_every_named_size_is_a_string_bucket_not_a_pixel_count():
    """``image_size`` names the longest dimension; the short edge follows."""
    for label, pixels in NAMED_OUTPUT_SIZES:
        assert isinstance(label, str) and label.endswith("K")
        assert isinstance(pixels, int)


# --- one request, two callers ------------------------------------------------


@pytest.mark.skipif(
    not sdk_available(), reason="the provider builds a real SDK config object"
)
def test_the_size_and_ratio_reach_the_provider_config():
    client = stub_client_returning(_png(64, 80, b"x"))
    provider = GoogleImagenProvider(client=client)
    provider.generate(build_request(_prompt()))

    config = client.models.calls[0]["config"]
    assert config.aspect_ratio == "3:4"
    assert config.image_size == "4K"


def test_two_calls_for_the_same_page_build_the_same_request():
    """Attempt number aside, the request must not drift between callers."""
    first = build_request(_prompt(), attempt=1)
    second = build_request(_prompt(), attempt=1)
    assert first == second
