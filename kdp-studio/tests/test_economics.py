"""The verified KDP royalty and printing-cost tables.

Every number here was checked against KDP's own pages on 2026-08-31 for
Amazon.com/US. The tests are written as arithmetic rather than behaviour
because that is what they are: an assertion here is a claim about dollars, and
getting one wrong prices a book to lose money on every copy sold.

Several cover mistakes earlier revisions actually made — a universal 60%
royalty, the long-book printing formula applied to short books, and an 8.5x11
coloring book priced as regular trim when KDP charges it as large.
"""

from __future__ import annotations

import pytest

from kdp.models.economics import (
    PRINTING_COSTS,
    ROYALTY_BANDS,
    EconomicsUnavailable,
    Ink,
    PaperStock,
    TrimClass,
    build_scenario,
    compare,
    for_book,
    printing_cost,
    royalty_rate,
)
from kdp.specs import get_paper, get_trim

BLACK_WHITE = dict(marketplace="amazon.com", ink=Ink.BLACK, stock=PaperStock.WHITE)


# --- trim classification ----------------------------------------------------


@pytest.mark.parametrize(
    ("trim", "cost_class"),
    [
        ("8.5x11", "large"),
        ("8x10", "large"),
        ("6x9", "regular"),
        ("5x8", "regular"),
        ("5.5x8.5", "regular"),
        ("6.14x9.21", "large"),
        ("8.25x6", "large"),
        ("8.25x8.25", "large"),
    ],
)
def test_trim_cost_classes(trim, cost_class):
    assert get_trim(trim).cost_class == cost_class


def test_the_coloring_book_default_is_large_trim():
    """The correction with the largest money consequence.

    8.5x11 is over 6.12in wide and over 9in high, so KDP prices it as large
    trim. Treating it as regular understates printing by 54 cents a copy — on a
    $5.99 book that is most of the margin.
    """
    assert get_trim("8.5x11").cost_class == "large"
    regular = printing_cost(60, trim_class=TrimClass.REGULAR, **BLACK_WHITE)
    large = printing_cost(60, trim_class=TrimClass.LARGE, **BLACK_WHITE)
    assert large - regular == pytest.approx(0.54)


def test_the_large_trim_rule_matches_kdp_s_published_split():
    """Cost class is derived from KDP's rule; this asserts it against the list."""
    from kdp.specs import trims_by_cost_class

    assert set(trims_by_cost_class("regular")) == {
        "5x8", "5.06x7.81", "5.25x8", "5.5x8.5", "6x9",
    }
    assert set(trims_by_cost_class("large")) == {
        "6.14x9.21", "6.69x9.61", "7x10", "7.44x9.69", "7.5x9.25", "8x10",
        "8.25x6", "8.25x8.25", "8.27x11.69", "8.5x8.5", "8.5x11",
    }


def test_the_rule_boundaries_are_exclusive():
    # 6x9 sits exactly on both limits and is regular; 6.14x9.21 exceeds width.
    assert get_trim("6x9").cost_class == "regular"
    assert get_trim("6.14x9.21").cost_class == "large"


# --- royalty rate by list price ---------------------------------------------


def test_5_99_paperback_earns_50_percent():
    assert royalty_rate(5.99) == 0.50


def test_9_98_paperback_earns_50_percent():
    assert royalty_rate(9.98) == 0.50


def test_9_99_paperback_earns_60_percent():
    assert royalty_rate(9.99) == 0.60


def test_royalty_is_not_a_universal_60_percent():
    assert [royalty_rate(p) for p in (0.99, 4.99, 5.99, 7.99, 9.98)] == [0.50] * 5


def test_a_price_between_the_bands_raises_rather_than_being_rounded():
    """KDP's rule covers $9.98 and below, $9.99 and above. Between is undefined."""
    with pytest.raises(EconomicsUnavailable, match="no royalty band covers"):
        royalty_rate(9.985)


def test_an_unverified_marketplace_raises_rather_than_guessing():
    with pytest.raises(EconomicsUnavailable, match="no verified royalty bands"):
        royalty_rate(9.99, marketplace="amazon.co.uk")


def test_only_verified_marketplaces_are_present():
    assert set(ROYALTY_BANDS) == {"amazon.com"}


# --- printing cost: black ink -----------------------------------------------


def test_50_page_regular_trim_costs_2_30():
    assert printing_cost(50, trim_class=TrimClass.REGULAR, **BLACK_WHITE) == pytest.approx(2.30)


def test_60_page_8_5x11_costs_2_84():
    """The coloring-book case, priced as the large trim it is."""
    assert printing_cost(60, trim_class=TrimClass.LARGE, **BLACK_WHITE) == pytest.approx(2.84)


def test_109_pages_is_still_the_flat_band():
    assert printing_cost(109, trim_class=TrimClass.REGULAR, **BLACK_WHITE) == pytest.approx(2.30)
    assert printing_cost(109, trim_class=TrimClass.LARGE, **BLACK_WHITE) == pytest.approx(2.84)


def test_exactly_110_pages_is_ambiguous_and_refuses():
    """KDP publishes the boundary twice: bands 24-110 AND 110-828.

    At exactly 110 pages the source states two different prices. Picking one
    silently would bury a discrepancy in KDP's own table under an arbitrary
    tie-break, and the resulting number would be believed. The model refuses
    and names both candidates.
    """
    for trim_class in (TrimClass.REGULAR, TrimClass.LARGE):
        with pytest.raises(EconomicsUnavailable, match="ambiguous at exactly 110 pages"):
            printing_cost(110, trim_class=trim_class, **BLACK_WHITE)


def test_the_110_refusal_names_both_published_prices():
    with pytest.raises(EconomicsUnavailable) as exc:
        printing_cost(110, trim_class=TrimClass.REGULAR, **BLACK_WHITE)
    message = str(exc.value)
    assert "2.30" in message and "2.32" in message
    assert "24-110" in message and "110-828" in message


def test_111_pages_uses_the_per_page_formula():
    assert printing_cost(111, trim_class=TrimClass.REGULAR, **BLACK_WHITE) == pytest.approx(
        1.00 + 111 * 0.012
    )
    assert printing_cost(111, trim_class=TrimClass.LARGE, **BLACK_WHITE) == pytest.approx(
        1.00 + 111 * 0.017
    )


def test_the_long_book_formula_is_not_applied_below_110_pages():
    """For a 24-page book the long formula gives $1.29 against an actual $2.30."""
    for pages in (24, 50, 80, 109):
        assert printing_cost(pages, trim_class=TrimClass.REGULAR, **BLACK_WHITE) == pytest.approx(2.30)
        assert printing_cost(pages, trim_class=TrimClass.REGULAR, **BLACK_WHITE) != pytest.approx(
            1.00 + pages * 0.012
        )


def test_the_flat_band_has_no_per_page_component():
    assert printing_cost(24, trim_class=TrimClass.REGULAR, **BLACK_WHITE) == printing_cost(
        109, trim_class=TrimClass.REGULAR, **BLACK_WHITE
    )


def test_cream_is_priced_the_same_as_white():
    """KDP publishes one black-ink table covering both stocks."""
    white = printing_cost(60, trim_class=TrimClass.LARGE, **BLACK_WHITE)
    cream = printing_cost(
        60, marketplace="amazon.com", ink=Ink.BLACK, stock=PaperStock.CREAM,
        trim_class=TrimClass.LARGE,
    )
    assert white == cream


def test_page_counts_outside_every_band_raise():
    for pages in (23, 829):
        with pytest.raises(EconomicsUnavailable, match="no verified printing cost"):
            printing_cost(pages, trim_class=TrimClass.REGULAR, **BLACK_WHITE)


# --- printing cost: groundwood and colour -----------------------------------


def test_groundwood_bands():
    kw = dict(marketplace="amazon.com", ink=Ink.BLACK, stock=PaperStock.GROUNDWOOD)
    assert printing_cost(100, trim_class=TrimClass.REGULAR, **kw) == pytest.approx(2.23)
    assert printing_cost(100, trim_class=TrimClass.LARGE, **kw) == pytest.approx(2.75)
    assert printing_cost(200, trim_class=TrimClass.REGULAR, **kw) == pytest.approx(1.00 + 200 * 0.0114)
    assert printing_cost(200, trim_class=TrimClass.LARGE, **kw) == pytest.approx(1.00 + 200 * 0.0162)


def test_groundwood_leaves_113_pages_unpriced():
    """KDP's bands jump 112 to 114. 113 has no published price."""
    kw = dict(marketplace="amazon.com", ink=Ink.BLACK, stock=PaperStock.GROUNDWOOD)
    assert printing_cost(112, trim_class=TrimClass.REGULAR, **kw) == pytest.approx(2.23)
    with pytest.raises(EconomicsUnavailable, match="no verified printing cost"):
        printing_cost(113, trim_class=TrimClass.REGULAR, **kw)
    assert printing_cost(114, trim_class=TrimClass.REGULAR, **kw) == pytest.approx(1.00 + 114 * 0.0114)


def test_premium_colour_bands():
    kw = dict(marketplace="amazon.com", ink=Ink.PREMIUM_COLOUR, stock=PaperStock.WHITE)
    assert printing_cost(30, trim_class=TrimClass.REGULAR, **kw) == pytest.approx(3.60)
    assert printing_cost(30, trim_class=TrimClass.LARGE, **kw) == pytest.approx(4.20)
    assert printing_cost(100, trim_class=TrimClass.REGULAR, **kw) == pytest.approx(1.00 + 100 * 0.065)
    assert printing_cost(100, trim_class=TrimClass.LARGE, **kw) == pytest.approx(1.00 + 100 * 0.08)


def test_premium_colour_leaves_41_pages_unpriced():
    """Bands jump 40 to 42."""
    kw = dict(marketplace="amazon.com", ink=Ink.PREMIUM_COLOUR, stock=PaperStock.WHITE)
    assert printing_cost(40, trim_class=TrimClass.REGULAR, **kw) == pytest.approx(3.60)
    with pytest.raises(EconomicsUnavailable):
        printing_cost(41, trim_class=TrimClass.REGULAR, **kw)
    assert printing_cost(42, trim_class=TrimClass.REGULAR, **kw) == pytest.approx(1.00 + 42 * 0.065)


def test_standard_colour_has_one_band_and_no_flat_fee():
    kw = dict(marketplace="amazon.com", ink=Ink.STANDARD_COLOUR, stock=PaperStock.WHITE)
    assert printing_cost(100, trim_class=TrimClass.REGULAR, **kw) == pytest.approx(1.00 + 100 * 0.0255)
    assert printing_cost(100, trim_class=TrimClass.LARGE, **kw) == pytest.approx(1.00 + 100 * 0.0402)
    for pages in (71, 601):
        with pytest.raises(EconomicsUnavailable):
            printing_cost(pages, trim_class=TrimClass.REGULAR, **kw)


@pytest.mark.parametrize(
    ("marketplace", "ink", "stock"),
    [
        ("amazon.co.uk", Ink.BLACK, PaperStock.WHITE),
        ("amazon.com", Ink.STANDARD_COLOUR, PaperStock.CREAM),
        ("amazon.com", Ink.PREMIUM_COLOUR, PaperStock.GROUNDWOOD),
    ],
)
def test_unverified_combinations_raise_rather_than_extrapolating(marketplace, ink, stock):
    """The absence of a row is a statement, not an oversight."""
    with pytest.raises(EconomicsUnavailable, match="no verified printing-cost table"):
        printing_cost(
            100, marketplace=marketplace, ink=ink, stock=stock,
            trim_class=TrimClass.REGULAR,
        )


def test_ten_cost_tables_are_recorded():
    assert len(PRINTING_COSTS) == 10
    assert all(m == "amazon.com" for m, _, _, _ in PRINTING_COSTS)


# --- the two together -------------------------------------------------------


def test_a_60_page_8_5x11_at_5_99_earns_about_15_cents():
    """The number that matters, and the one three earlier revisions got wrong.

    50% of $5.99 is $2.995. Large-trim printing is $2.84. That leaves $0.155 a
    copy before any other deduction — and the model does not claim to know what
    those other deductions are.
    """
    scenario = build_scenario(
        5.99, 60, trim_class=TrimClass.LARGE, **BLACK_WHITE
    )
    assert scenario.royalty_rate == 0.50
    assert scenario.print_cost_usd == pytest.approx(2.84)
    assert scenario.net_royalty_usd == pytest.approx(0.155)
    assert scenario.viable is True


def test_the_same_book_priced_as_regular_trim_would_overstate_the_margin():
    """What the previous revision reported, against what is true."""
    wrong = build_scenario(5.99, 60, trim_class=TrimClass.REGULAR, **BLACK_WHITE)
    right = build_scenario(5.99, 60, trim_class=TrimClass.LARGE, **BLACK_WHITE)
    assert wrong.net_royalty_usd == pytest.approx(0.695)
    assert right.net_royalty_usd == pytest.approx(0.155)
    assert wrong.net_royalty_usd > right.net_royalty_usd * 4


def test_crossing_the_royalty_step_is_worth_more_than_the_price_difference():
    below = build_scenario(9.98, 60, trim_class=TrimClass.LARGE, **BLACK_WHITE)
    at = build_scenario(9.99, 60, trim_class=TrimClass.LARGE, **BLACK_WHITE)
    assert at.net_royalty_usd - below.net_royalty_usd > 0.59


def test_a_short_cheap_large_trim_book_can_be_unviable():
    scenario = build_scenario(4.99, 30, trim_class=TrimClass.LARGE, **BLACK_WHITE)
    assert scenario.net_royalty_usd < 0
    assert scenario.viable is False


# --- resolving from the book itself -----------------------------------------


def test_for_book_resolves_ink_stock_and_trim_class():
    econ = for_book(
        [5.99], trim=get_trim("8.5x11"), paper=get_paper("bw_white"), page_count=60
    )
    scenario = econ.scenarios[0]
    assert scenario.trim_class is TrimClass.LARGE
    assert scenario.ink is Ink.BLACK
    assert scenario.stock is PaperStock.WHITE
    assert scenario.net_royalty_usd == pytest.approx(0.155)


def test_for_book_prices_a_regular_trim_book_differently():
    econ = for_book(
        [5.99], trim=get_trim("6x9"), paper=get_paper("bw_white"), page_count=60
    )
    assert econ.scenarios[0].net_royalty_usd == pytest.approx(0.695)


def test_for_book_refuses_a_110_page_book():
    econ = for_book(
        [9.99], trim=get_trim("8.5x11"), paper=get_paper("bw_white"), page_count=110
    )
    assert econ.scenarios == ()
    assert "ambiguous at exactly 110" in econ.unavailable[0]


def test_omitting_the_trim_class_prices_nothing_and_says_why():
    econ = compare([5.99, 9.99], 60)
    assert econ.scenarios == ()
    assert "No trim class supplied" in econ.unavailable[0]


def test_a_scenario_records_the_resolved_figures_rather_than_recomputing():
    """A corrected table must not silently change a stored scenario's meaning."""
    scenario = build_scenario(9.99, 200, trim_class=TrimClass.LARGE, **BLACK_WHITE)
    restored = type(scenario).from_dict(scenario.to_dict())
    assert restored.royalty_rate == scenario.royalty_rate
    assert restored.print_cost_usd == scenario.print_cost_usd
    assert restored.trim_class is TrimClass.LARGE


def test_other_deductions_are_declared_as_not_modelled():
    """The model must not imply a payout it does not compute."""
    econ = for_book(
        [5.99], trim=get_trim("8.5x11"), paper=get_paper("bw_white"), page_count=60
    )
    note = next(a for a in econ.assumptions if a.key == "other_deductions")
    assert "not modelled" in str(note.value)
    assert econ.confidence.value == "estimated"
