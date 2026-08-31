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


@pytest.mark.parametrize(
    ("stock", "caliper"),
    [
        ("bw_white", 0.002252),
        ("bw_cream", 0.0025),
        ("bw_groundwood", 0.00235),
        # KDP's submission guideline gives one caliper for colour paper. The
        # tiers differ in printing cost, not in paper thickness.
        ("standard_colour", 0.002347),
        ("premium_colour", 0.002347),
    ],
)
def test_spine_coefficients_are_the_verified_ones(stock, caliper):
    assert get_paper(stock).caliper_in == pytest.approx(caliper)
    assert get_paper(stock).spine_width_in(200) == pytest.approx(200 * caliper)


def test_both_colour_tiers_share_one_spine_caliper():
    """Verified 2026-08-31: the guideline states one figure for colour paper.

    A revision in between carried 0.002252 for standard colour on an earlier
    instruction. That is superseded — KDP separates the tiers in printing cost,
    not in thickness.
    """
    assert get_paper("standard_colour").spine_width_in(300) == pytest.approx(
        get_paper("premium_colour").spine_width_in(300)
    )


def test_each_paper_declares_its_ink_and_stock():
    """The bridge economics uses to find a cost table from a book's paper."""
    assert (get_paper("bw_white").ink, get_paper("bw_white").stock) == ("black", "white")
    assert get_paper("bw_groundwood").stock == "groundwood"
    assert get_paper("premium_colour").ink == "premium_colour"


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


def test_spine_text_is_withheld_at_79_pages():
    """KDP prints no spine text on 79 pages or fewer.

    An earlier revision used 100 here, a conservative guess taken from
    disagreeing secondary sources. It silently withheld spine text from every
    book between 80 and 99 pages.
    """
    trim, paper = get_trim("8.5x11"), get_paper("bw_white")
    assert cover_geometry(trim, paper, 79).spine_text_allowed is False


def test_spine_text_is_permitted_at_80_pages():
    trim, paper = get_trim("8.5x11"), get_paper("bw_white")
    assert cover_geometry(trim, paper, 80).spine_text_allowed is True


def test_the_spine_text_boundary_is_exactly_79_to_80():
    trim, paper = get_trim("8.5x11"), get_paper("bw_white")
    allowed = [
        pages
        for pages in range(76, 84)
        if cover_geometry(trim, paper, pages).spine_text_allowed
    ]
    assert allowed == [80, 81, 82, 83]


def test_narrow_spine_reports_no_usable_text_width():
    trim, paper = get_trim("8.5x11"), get_paper("bw_white")
    # 40 pages is ~0.09" of spine, less than the two 0.0625" safety margins.
    assert cover_geometry(trim, paper, 40).spine_text_width_in == 0.0


def test_paper_page_count_bounds():
    assert get_paper("bw_white").supports_page_count(24) is True
    assert get_paper("bw_white").supports_page_count(23) is False
    # Standard colour starts at 72; premium runs the full range.
    assert get_paper("standard_colour").supports_page_count(60) is False
    assert get_paper("standard_colour").supports_page_count(601) is False
    assert get_paper("premium_colour").supports_page_count(601) is True


def test_the_trim_table_matches_kdp_s_published_list():
    """Reconciled 2026-08-31: 16 US paperback sizes, 5 regular and 11 large."""
    from kdp.specs import TRIM_SIZES, trims_by_cost_class

    assert len(TRIM_SIZES) == 16
    assert "8.25x8.25" in TRIM_SIZES  # was missing before the reconciliation
    assert len(trims_by_cost_class("regular")) == 5
    assert len(trims_by_cost_class("large")) == 11


def test_large_trim_is_derived_from_kdp_s_rule():
    from kdp.specs import LARGE_TRIM_MAX_HEIGHT_IN, LARGE_TRIM_MAX_WIDTH_IN, TRIM_SIZES

    for trim in TRIM_SIZES.values():
        expected = (
            "large"
            if trim.width_in > LARGE_TRIM_MAX_WIDTH_IN
            or trim.height_in > LARGE_TRIM_MAX_HEIGHT_IN
            else "regular"
        )
        assert trim.cost_class == expected, trim.key


def test_unknown_trim_and_paper_are_rejected_by_name():
    with pytest.raises(SpecError, match="unknown trim size"):
        get_trim("9x9")
    with pytest.raises(SpecError, match="unknown paper"):
        get_paper("papyrus")
