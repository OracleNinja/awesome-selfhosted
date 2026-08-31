"""The verified KDP royalty and printing-cost tables.

Every number here was checked against KDP's own documentation. The tests are
written as arithmetic rather than behaviour because that is what they are: an
assertion here is a claim about dollars, and getting one wrong means a book is
priced to lose money on every copy sold.

Two of these cover mistakes the previous revision actually made — a universal
60% royalty, and the long-book printing formula applied to short books.
"""

from __future__ import annotations

import pytest

from kdp.models.economics import (
    PRINTING_COSTS,
    ROYALTY_BANDS,
    EconomicsUnavailable,
    Ink,
    TrimClass,
    build_scenario,
    compare,
    printing_cost,
    royalty_rate,
)

REGULAR = dict(marketplace="amazon.com", ink=Ink.BLACK, trim_class=TrimClass.REGULAR)


# --- royalty rate by list price ---------------------------------------------


def test_5_99_paperback_earns_50_percent():
    """The mistake the previous revision made, at the price it was aimed at."""
    assert royalty_rate(5.99) == 0.50


def test_9_98_paperback_earns_50_percent():
    assert royalty_rate(9.98) == 0.50


def test_9_99_paperback_earns_60_percent():
    assert royalty_rate(9.99) == 0.60


def test_the_royalty_step_is_exactly_at_9_99():
    assert royalty_rate(9.98) == 0.50
    assert royalty_rate(9.99) == 0.60
    assert royalty_rate(10.00) == 0.60


def test_a_price_between_the_bands_raises_rather_than_being_rounded():
    """KDP's rule is stated for $9.98 and below and $9.99 and above.

    The sliver between them is undefined, and a real list price never lands
    there. Silently assigning it a rate would be inventing policy.
    """
    with pytest.raises(EconomicsUnavailable, match="no royalty band covers"):
        royalty_rate(9.985)


def test_royalty_is_not_a_universal_60_percent():
    below = [p for p in (0.99, 4.99, 5.99, 7.99, 9.98) if royalty_rate(p) != 0.60]
    assert len(below) == 5


def test_an_unverified_marketplace_raises_rather_than_guessing():
    with pytest.raises(EconomicsUnavailable, match="no verified royalty bands"):
        royalty_rate(9.99, marketplace="amazon.co.uk")


def test_only_verified_marketplaces_are_present():
    assert set(ROYALTY_BANDS) == {"amazon.com"}


# --- printing cost by page count --------------------------------------------


def test_50_page_black_ink_regular_trim_costs_2_30():
    assert printing_cost(50, **REGULAR) == pytest.approx(2.30)


def test_110_page_black_ink_regular_trim_costs_2_30():
    """The last page of the flat band."""
    assert printing_cost(110, **REGULAR) == pytest.approx(2.30)


def test_111_page_black_ink_regular_trim_switches_to_the_per_page_formula():
    assert printing_cost(111, **REGULAR) == pytest.approx(1.00 + 111 * 0.012)


def test_the_long_book_formula_is_not_applied_below_111_pages():
    """The band boundary, from both sides.

    Charging $1.00 + $0.012/page for a 110-page book gives $2.32, which is
    close enough to $2.30 to look right and wrong enough to matter across a
    catalogue. For a 24-page book it gives $1.29 against an actual $2.30 —
    a dollar of margin that does not exist.
    """
    for pages in (24, 50, 80, 110):
        flat = printing_cost(pages, **REGULAR)
        long_formula = 1.00 + pages * 0.012
        assert flat == pytest.approx(2.30)
        assert flat != pytest.approx(long_formula)


def test_the_flat_band_has_no_per_page_component():
    assert printing_cost(24, **REGULAR) == printing_cost(110, **REGULAR)


def test_page_counts_outside_the_verified_range_raise():
    with pytest.raises(EconomicsUnavailable, match="no verified printing cost"):
        printing_cost(23, **REGULAR)
    with pytest.raises(EconomicsUnavailable, match="no verified printing cost"):
        printing_cost(829, **REGULAR)


@pytest.mark.parametrize(
    ("marketplace", "ink", "trim_class"),
    [
        ("amazon.co.uk", Ink.BLACK, TrimClass.REGULAR),
        ("amazon.com", Ink.STANDARD_COLOUR, TrimClass.REGULAR),
        ("amazon.com", Ink.PREMIUM_COLOUR, TrimClass.REGULAR),
        ("amazon.com", Ink.BLACK, TrimClass.LARGE),
    ],
)
def test_unverified_combinations_raise_rather_than_extrapolating(
    marketplace, ink, trim_class
):
    """The absence of a row is a statement, not an oversight."""
    with pytest.raises(EconomicsUnavailable, match="no verified printing-cost table"):
        printing_cost(100, marketplace=marketplace, ink=ink, trim_class=trim_class)


def test_only_the_verified_combination_is_present():
    assert set(PRINTING_COSTS) == {("amazon.com", Ink.BLACK, TrimClass.REGULAR)}


# --- the two together -------------------------------------------------------


def test_a_5_99_60_page_book_earns_its_true_margin():
    """What the previous revision would have got wrong, end to end.

    Old model: 60% of $5.99 minus ($1.00 + 60 x $0.012) = $3.594 - $1.72 = $1.87.
    Verified:  50% of $5.99 minus $2.30                 = $2.995 - $2.30 = $0.695.

    A book reported as earning $1.87 a copy actually earns $0.70 — the old
    figure was nearly three times the real one.
    """
    scenario = build_scenario(5.99, 60, trim_class=TrimClass.REGULAR)
    assert scenario.royalty_rate == 0.50
    assert scenario.print_cost_usd == pytest.approx(2.30)
    assert scenario.net_royalty_usd == pytest.approx(0.695)
    assert scenario.viable is True


def test_crossing_the_royalty_step_is_worth_more_than_the_price_difference():
    """$9.99 beats $9.98 by a penny of list and 60 cents of margin."""
    below = build_scenario(9.98, 60, trim_class=TrimClass.REGULAR)
    at = build_scenario(9.99, 60, trim_class=TrimClass.REGULAR)
    assert at.net_royalty_usd - below.net_royalty_usd > 0.59


def test_a_short_cheap_book_can_be_unviable():
    scenario = build_scenario(3.99, 30, trim_class=TrimClass.REGULAR)
    assert scenario.net_royalty_usd < 0
    assert scenario.viable is False


def test_compare_records_why_a_price_could_not_be_computed():
    econ = compare([9.99], 900, trim_class=TrimClass.REGULAR)
    assert econ.scenarios == ()
    assert econ.unavailable and "no verified printing cost" in econ.unavailable[0]


def test_omitting_the_trim_class_prices_nothing_and_says_why():
    econ = compare([5.99, 9.99], 60)
    assert econ.scenarios == ()
    assert "No trim class supplied" in econ.unavailable[0]


def test_a_scenario_records_the_resolved_figures_rather_than_recomputing():
    """A corrected table must not silently change a stored scenario's meaning.

    It produces a *different* scenario, which changes the fingerprint, which
    invalidates any approval granted against the old numbers.
    """
    scenario = build_scenario(9.99, 200, trim_class=TrimClass.REGULAR)
    restored = type(scenario).from_dict(scenario.to_dict())
    assert restored.royalty_rate == scenario.royalty_rate
    assert restored.print_cost_usd == scenario.print_cost_usd
    assert restored.net_royalty_usd == scenario.net_royalty_usd
