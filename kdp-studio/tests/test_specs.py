"""Print-geometry maths.

These are the numbers a physical book is manufactured from, so they are tested
as arithmetic rather than as behaviour: an assertion here is a claim about
inches.
"""

from __future__ import annotations

import pytest

from kdp.errors import SpecError
from kdp.specs import (
    BLEED_IN,
    cover_geometry,
    get_paper,
    get_trim,
    gutter_in,
    interior_geometry,
)


def test_bleed_grows_the_page_asymmetrically():
    # The trap: bleed adds 0.125" to the width but 0.25" to the height,
    # because it applies at both the top and the bottom.
    geometry = interior_geometry(get_trim("6x9"), 100, bleed=True)
    assert geometry.page_width_in == pytest.approx(6.125)
    assert geometry.page_height_in == pytest.approx(9.25)


def test_no_bleed_page_equals_trim():
    geometry = interior_geometry(get_trim("6x9"), 100, bleed=False)
    assert geometry.page_width_in == pytest.approx(6.0)
    assert geometry.page_height_in == pytest.approx(9.0)


def test_bleed_requires_a_wider_outer_margin():
    trim = get_trim("8.5x11")
    assert interior_geometry(trim, 60, bleed=False).outer_margin_in == pytest.approx(0.25)
    assert interior_geometry(trim, 60, bleed=True).outer_margin_in == pytest.approx(0.375)


@pytest.mark.parametrize(
    ("pages", "expected"),
    [(24, 0.375), (150, 0.375), (151, 0.5), (300, 0.5), (301, 0.625), (828, 0.875)],
)
def test_gutter_widens_with_page_count(pages, expected):
    assert gutter_in(pages) == pytest.approx(expected)


def test_gutter_is_undefined_outside_the_printable_range():
    with pytest.raises(SpecError):
        gutter_in(12)
    with pytest.raises(SpecError):
        gutter_in(1000)


def test_live_area_subtracts_gutter_once_and_outer_margin_twice():
    geometry = interior_geometry(get_trim("8.5x11"), 60, bleed=True)
    assert geometry.live_width_in == pytest.approx(8.5 - 0.375 - 0.375)
    assert geometry.live_height_in == pytest.approx(11.0 - 0.75)


def test_spine_width_is_page_count_times_caliper():
    paper = get_paper("bw_white")
    assert paper.spine_width_in(100) == pytest.approx(0.2252)
    assert paper.spine_width_in(0) == pytest.approx(0.0)


def test_cover_canvas_spans_both_panels_the_spine_and_the_bleed():
    trim, paper = get_trim("8.5x11"), get_paper("bw_white")
    cover = cover_geometry(trim, paper, 100)
    expected = (2 * BLEED_IN) + (2 * 8.5) + paper.spine_width_in(100)
    assert cover.total_width_in == pytest.approx(expected)
    assert cover.total_height_in == pytest.approx(11.25)


def test_front_panel_starts_after_the_back_cover_and_spine():
    trim, paper = get_trim("6x9"), get_paper("bw_cream")
    cover = cover_geometry(trim, paper, 200)
    assert cover.front_panel_origin_x_in == pytest.approx(
        BLEED_IN + 6.0 + paper.spine_width_in(200)
    )


def test_spine_text_is_withheld_on_thin_books():
    trim, paper = get_trim("8.5x11"), get_paper("bw_white")
    assert cover_geometry(trim, paper, 60).spine_text_allowed is False
    assert cover_geometry(trim, paper, 100).spine_text_allowed is True


def test_narrow_spine_reports_no_usable_text_width():
    trim, paper = get_trim("8.5x11"), get_paper("bw_white")
    # 40 pages is ~0.09" of spine, less than the two 0.0625" safety margins.
    assert cover_geometry(trim, paper, 40).spine_text_width_in == 0.0


def test_paper_page_count_bounds():
    assert get_paper("bw_white").supports_page_count(24) is True
    assert get_paper("bw_white").supports_page_count(23) is False
    assert get_paper("standard_colour").supports_page_count(60) is False
    assert get_paper("premium_colour").supports_page_count(601) is False


def test_unknown_trim_and_paper_are_rejected_by_name():
    with pytest.raises(SpecError, match="unknown trim size"):
        get_trim("9x9")
    with pytest.raises(SpecError, match="unknown paper"):
        get_paper("papyrus")
