"""Book economics: royalty rate, printing cost, and what a copy actually earns.

Both halves of the calculation are *tabular*, not formulaic, because KDP's are:

**The royalty rate depends on the list price.** On amazon.com a paperback under
$9.99 earns 50%, not 60%. An earlier revision of this module assumed 60%
universally, which overstated the return on every book priced below that line —
including $5.99, the price this pipeline was most likely to be pointed at.

**The printing cost depends on the page count, and changes shape.** Short books
are charged a flat fee with no per-page component at all; longer ones switch to
a fixed charge plus a rate. Applying the long-book formula to a short book
under-states the cost by more than a dollar, which is most of the margin.

Both tables are marketplace-, ink- and trim-class-specific, and only the
combinations actually verified against KDP's documentation are present. Asking
for one that is not raises rather than interpolating: a plausible-looking number
for an unverified combination is worse than no number, because it gets believed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..errors import KDPError, ModelError
from .strategy import Confidence


class EconomicsUnavailable(KDPError):
    """No verified figures exist for the combination requested."""


class Ink(str, Enum):
    BLACK = "black"
    STANDARD_COLOUR = "standard_colour"
    PREMIUM_COLOUR = "premium_colour"


class PaperStock(str, Enum):
    WHITE = "white"
    CREAM = "cream"
    GROUNDWOOD = "groundwood"


class TrimClass(str, Enum):
    """KDP prices regular and large trims differently.

    Which sizes fall into which class is derived from KDP's own rule in
    ``kdp/specs/trim.py``: large is anything over 6.12 inches wide or over 9
    inches high. For an 8.5x11 book — the coloring-book default — the
    difference is 54 cents on every copy.
    """

    REGULAR = "regular"
    LARGE = "large"


# --- printing cost ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PrintingBand:
    """A page range and what printing costs inside it.

    ``per_page_usd`` of zero is not a placeholder: short books really are
    charged a flat fee.
    """

    min_pages: int
    max_pages: int
    fixed_usd: float
    per_page_usd: float

    def contains(self, pages: int) -> bool:
        return self.min_pages <= pages <= self.max_pages

    def cost(self, pages: int) -> float:
        return round(self.fixed_usd + (pages * self.per_page_usd), 4)


@dataclass(frozen=True, slots=True)
class PrintingCostTable:
    """Printing cost for one marketplace, ink and trim class."""

    marketplace: str
    ink: Ink
    stock: PaperStock
    trim_class: TrimClass
    currency: str
    bands: tuple[PrintingBand, ...]

    def cost(self, pages: int) -> float:
        matches = [band for band in self.bands if band.contains(pages)]
        covered = ", ".join(f"{b.min_pages}-{b.max_pages}" for b in self.bands)
        where = (
            f"{self.marketplace} ({self.ink.value} on {self.stock.value}, "
            f"{self.trim_class.value} trim)"
        )

        if len(matches) > 1:
            # KDP's own table labels adjacent bands with the same boundary page
            # count, so at that page count two different prices are documented.
            # Picking one silently would bury a discrepancy in the source under
            # an arbitrary tie-break, and the number would be believed.
            options = ", ".join(f"${b.cost(pages):.4f} (band {b.min_pages}-{b.max_pages})"
                                for b in matches)
            raise EconomicsUnavailable(
                f"KDP's published table is ambiguous at exactly {pages} pages "
                f"for {where}: {len(matches)} bands claim it, giving {options}. "
                "The boundary is stated twice in the source rather than once. "
                "Confirm which band KDP actually applies at this page count "
                "before pricing a book of this length."
            )
        if not matches:
            raise EconomicsUnavailable(
                f"no verified printing cost for a {pages}-page book on {where}; "
                f"verified page ranges are {covered}"
            )
        return matches[0].cost(pages)


#: Verified against KDP's printing cost & royalty page on 2026-08-31 for
#: amazon.com. Keyed by marketplace, ink, paper stock and trim class, because
#: KDP prices on all four. Nothing is interpolated: a combination absent from
#: this table raises, because a plausible number for an unchecked combination
#: is worse than no number — it gets believed.
#:
#: Two anomalies are transcribed faithfully rather than tidied away, and both
#: make the model refuse rather than guess:
#:
#: * Black ink bands are published as 24-110 *and* 110-828, so exactly 110
#:   pages is claimed by both at two different prices.
#: * Groundwood jumps 112 to 114, and premium colour jumps 40 to 42, leaving
#:   113 and 41 pages with no published price at all.
def _bands(fixed: float, per_page: float, lo: int, hi: int) -> PrintingBand:
    return PrintingBand(lo, hi, fixed, per_page)


PRINTING_COSTS: dict[
    tuple[str, Ink, PaperStock, TrimClass], PrintingCostTable
] = {}


def _register(
    marketplace: str,
    ink: Ink,
    stock: PaperStock,
    trim_class: TrimClass,
    bands: tuple[PrintingBand, ...],
) -> None:
    PRINTING_COSTS[(marketplace, ink, stock, trim_class)] = PrintingCostTable(
        marketplace=marketplace,
        ink=ink,
        stock=stock,
        trim_class=trim_class,
        currency="USD",
        bands=bands,
    )


# Black ink on white or cream. KDP publishes one table for both stocks.
for _stock in (PaperStock.WHITE, PaperStock.CREAM):
    _register(
        "amazon.com", Ink.BLACK, _stock, TrimClass.REGULAR,
        (_bands(2.30, 0.0, 24, 110), _bands(1.00, 0.012, 110, 828)),
    )
    _register(
        "amazon.com", Ink.BLACK, _stock, TrimClass.LARGE,
        (_bands(2.84, 0.0, 24, 110), _bands(1.00, 0.017, 110, 828)),
    )
del _stock

# Black ink on groundwood.
_register(
    "amazon.com", Ink.BLACK, PaperStock.GROUNDWOOD, TrimClass.REGULAR,
    (_bands(2.23, 0.0, 24, 112), _bands(1.00, 0.0114, 114, 828)),
)
_register(
    "amazon.com", Ink.BLACK, PaperStock.GROUNDWOOD, TrimClass.LARGE,
    (_bands(2.75, 0.0, 24, 112), _bands(1.00, 0.0162, 114, 828)),
)

# Premium colour on white.
_register(
    "amazon.com", Ink.PREMIUM_COLOUR, PaperStock.WHITE, TrimClass.REGULAR,
    (_bands(3.60, 0.0, 24, 40), _bands(1.00, 0.065, 42, 828)),
)
_register(
    "amazon.com", Ink.PREMIUM_COLOUR, PaperStock.WHITE, TrimClass.LARGE,
    (_bands(4.20, 0.0, 24, 40), _bands(1.00, 0.08, 42, 828)),
)

# Standard colour on white. One band, no flat-fee range.
_register(
    "amazon.com", Ink.STANDARD_COLOUR, PaperStock.WHITE, TrimClass.REGULAR,
    (_bands(1.00, 0.0255, 72, 600),),
)
_register(
    "amazon.com", Ink.STANDARD_COLOUR, PaperStock.WHITE, TrimClass.LARGE,
    (_bands(1.00, 0.0402, 72, 600),),
)


def printing_cost(
    pages: int,
    *,
    marketplace: str = "amazon.com",
    ink: Ink = Ink.BLACK,
    stock: PaperStock = PaperStock.WHITE,
    trim_class: TrimClass,
) -> float:
    """Printing cost per copy, or raise if the combination is not verified."""
    table = PRINTING_COSTS.get((marketplace, ink, stock, trim_class))
    if table is None:
        known = ", ".join(
            f"{m}/{i.value}/{s.value}/{t.value}"
            for m, i, s, t in sorted(
                PRINTING_COSTS,
                key=lambda k: (k[0], k[1].value, k[2].value, k[3].value),
            )
        )
        raise EconomicsUnavailable(
            f"no verified printing-cost table for {marketplace} ({ink.value} "
            f"on {stock.value}, {trim_class.value} trim). Verified "
            f"combinations: {known}. KDP prices on all four of those, and this "
            "combination has not been checked — extrapolating from another row "
            "would produce a number that looks authoritative and is not."
        )
    return table.cost(pages)


# --- royalty rate -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoyaltyBand:
    """A list-price range and the royalty rate that applies inside it.

    Both bounds are inclusive and both are stated. The bands deliberately do
    not tile the number line: KDP's documented rule is "$9.98 or below" and
    "$9.99 or above", which leaves the sliver between them undefined. A price
    that lands there matches nothing and raises, rather than being rounded into
    whichever neighbour the comparison happens to reach first.
    """

    min_price_usd: float
    #: Inclusive upper bound. None means "no upper bound".
    max_price_usd: float | None
    rate: float

    def contains(self, price: float) -> bool:
        if price < self.min_price_usd:
            return False
        return self.max_price_usd is None or price <= self.max_price_usd


#: Standard distribution. Expanded distribution pays a different rate and is
#: not modelled, because it was not verified.
ROYALTY_BANDS: dict[str, tuple[RoyaltyBand, ...]] = {
    "amazon.com": (
        RoyaltyBand(min_price_usd=0.01, max_price_usd=9.98, rate=0.50),
        RoyaltyBand(min_price_usd=9.99, max_price_usd=None, rate=0.60),
    ),
}


def royalty_rate(list_price_usd: float, *, marketplace: str = "amazon.com") -> float:
    """The royalty rate for a tax-exclusive list price.

    Not a constant. On amazon.com the rate steps from 50% to 60% at $9.99, so a
    $5.99 book earns half its list price before printing, not sixty percent.
    """
    bands = ROYALTY_BANDS.get(marketplace)
    if bands is None:
        raise EconomicsUnavailable(
            f"no verified royalty bands for {marketplace}; verified "
            f"marketplaces: {', '.join(sorted(ROYALTY_BANDS))}"
        )
    for band in bands:
        if band.contains(list_price_usd):
            return band.rate
    covered = "; ".join(
        f"{b.min_price_usd}-{b.max_price_usd if b.max_price_usd is not None else 'up'}"
        f" at {b.rate:.0%}"
        for b in bands
    )
    raise EconomicsUnavailable(
        f"no royalty band covers a list price of {list_price_usd} on "
        f"{marketplace}. Verified bands: {covered}. KDP list prices carry two "
        "decimal places, so a value between bands is a malformed price rather "
        "than a rate to be guessed at."
    )


# --- scenarios --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Assumption:
    """One input to the model, and how well it is known."""

    key: str
    value: float | str
    confidence: Confidence
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "confidence": self.confidence.value,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class PricingScenario:
    """One list price, and what a copy would earn under verified figures.

    The rate and the printing cost are *resolved* when the scenario is built,
    not recomputed on access, so a stored scenario cannot silently change
    meaning when a table is corrected. A corrected table produces a different
    scenario, which changes the manifest fingerprint, which invalidates any
    approval that was granted against the old numbers.
    """

    label: str
    #: Tax-exclusive list price, which is what KDP's royalty formula uses.
    list_price_usd: float
    page_count: int
    royalty_rate: float
    print_cost_usd: float
    marketplace: str = "amazon.com"
    ink: Ink = Ink.BLACK
    stock: PaperStock = PaperStock.WHITE
    trim_class: TrimClass = TrimClass.REGULAR
    currency: str = "USD"

    def __post_init__(self) -> None:
        if self.list_price_usd <= 0:
            raise ModelError(f"scenario {self.label!r} has a non-positive list price")
        if self.page_count <= 0:
            raise ModelError(f"scenario {self.label!r} has a non-positive page count")

    @property
    def gross_royalty_usd(self) -> float:
        return round(self.list_price_usd * self.royalty_rate, 4)

    @property
    def net_royalty_usd(self) -> float:
        """What the author receives per copy. Can be negative."""
        return round(self.gross_royalty_usd - self.print_cost_usd, 4)

    @property
    def viable(self) -> bool:
        return self.net_royalty_usd > 0

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "list_price_usd": self.list_price_usd,
            "page_count": self.page_count,
            "marketplace": self.marketplace,
            "ink": self.ink.value,
            "stock": self.stock.value,
            "trim_class": self.trim_class.value,
            "currency": self.currency,
            "royalty_rate": self.royalty_rate,
            "print_cost_usd": self.print_cost_usd,
            "gross_royalty_usd": self.gross_royalty_usd,
            "net_royalty_usd": self.net_royalty_usd,
            "viable": self.viable,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PricingScenario:
        try:
            return cls(
                label=data["label"],
                list_price_usd=float(data["list_price_usd"]),
                page_count=int(data["page_count"]),
                royalty_rate=float(data["royalty_rate"]),
                print_cost_usd=float(data["print_cost_usd"]),
                marketplace=data.get("marketplace", "amazon.com"),
                ink=Ink(data.get("ink", Ink.BLACK.value)),
                stock=PaperStock(data.get("stock", PaperStock.WHITE.value)),
                trim_class=TrimClass(data.get("trim_class", TrimClass.REGULAR.value)),
                currency=data.get("currency", "USD"),
            )
        except KeyError as exc:
            raise ModelError(f"pricing scenario missing field {exc.args[0]!r}") from None


def build_scenario(
    list_price_usd: float,
    page_count: int,
    *,
    marketplace: str = "amazon.com",
    ink: Ink = Ink.BLACK,
    stock: PaperStock = PaperStock.WHITE,
    trim_class: TrimClass,
    label: str | None = None,
) -> PricingScenario:
    """Resolve a scenario from the verified tables, or raise.

    Shape is validated before the tables are consulted, so a malformed price
    reports as a malformed price rather than as an unrecognised royalty band.
    """
    if list_price_usd <= 0:
        raise ModelError(f"list price must be positive, got {list_price_usd}")
    if page_count <= 0:
        raise ModelError(f"page count must be positive, got {page_count}")

    return PricingScenario(
        label=label or f"${list_price_usd:.2f}",
        list_price_usd=list_price_usd,
        page_count=page_count,
        royalty_rate=royalty_rate(list_price_usd, marketplace=marketplace),
        print_cost_usd=printing_cost(
            page_count,
            marketplace=marketplace,
            ink=ink,
            stock=stock,
            trim_class=trim_class,
        ),
        marketplace=marketplace,
        ink=ink,
        stock=stock,
        trim_class=trim_class,
    )


@dataclass(frozen=True, slots=True)
class Economics:
    """Pricing scenarios for one book, plus what could not be priced.

    ``confidence`` is the weakest confidence among the assumptions: a
    projection is only as sound as its shakiest input, and rounding that up
    would be exactly the misrepresentation this model exists to prevent.
    """

    selected: str | None = None
    scenarios: tuple[PricingScenario, ...] = field(default_factory=tuple)
    assumptions: tuple[Assumption, ...] = field(default_factory=tuple)
    #: Human-readable reasons a price could not be computed. Never empty when
    #: ``scenarios`` is empty and prices were asked for.
    unavailable: tuple[str, ...] = field(default_factory=tuple)

    def scenario(self, label: str) -> PricingScenario | None:
        for scenario in self.scenarios:
            if scenario.label == label:
                return scenario
        return None

    @property
    def selected_scenario(self) -> PricingScenario | None:
        return self.scenario(self.selected) if self.selected else None

    @property
    def confidence(self) -> Confidence:
        order = [
            Confidence.OBSERVED,
            Confidence.INFERRED,
            Confidence.ESTIMATED,
            Confidence.UNKNOWN,
        ]
        if not self.assumptions:
            return Confidence.UNKNOWN
        return max((a.confidence for a in self.assumptions), key=order.index)

    def to_dict(self) -> dict:
        return {
            "selected": self.selected,
            "scenarios": [s.to_dict() for s in self.scenarios],
            "assumptions": [a.to_dict() for a in self.assumptions],
            "unavailable": list(self.unavailable),
            "confidence": self.confidence.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Economics:
        return cls(
            selected=data.get("selected"),
            scenarios=tuple(
                PricingScenario.from_dict(s) for s in data.get("scenarios", ())
            ),
            assumptions=tuple(
                Assumption(
                    key=a["key"],
                    value=a["value"],
                    confidence=Confidence(a.get("confidence", "unknown")),
                    source=a.get("source", ""),
                )
                for a in data.get("assumptions", ())
            ),
            unavailable=tuple(data.get("unavailable", ())),
        )


def default_assumptions(
    *, marketplace: str = "amazon.com", ink: Ink = Ink.BLACK,
    trim_class: TrimClass | None = None,
) -> tuple[Assumption, ...]:
    """What the model rests on, and how well each part is known."""
    verified = (
        "Verified against KDP Help 2026-08-31; see kdp/specs/revision.py "
        "table 'royalty'."
    )
    return (
        Assumption(
            "royalty_bands",
            f"{marketplace}: 50% at $9.98 or below, 60% at $9.99 or above",
            Confidence.OBSERVED,
            verified,
        ),
        Assumption(
            "printing_cost_bands",
            f"{marketplace} {ink.value}, published bands by page count",
            Confidence.OBSERVED,
            verified,
        ),
        Assumption(
            "trim_class",
            trim_class.value if trim_class else "unresolved",
            Confidence.OBSERVED if trim_class else Confidence.UNKNOWN,
            "Derived from KDP's rule: large trim is over 6.12in wide or over "
            "9in high.",
        ),
        Assumption(
            "other_deductions",
            "not modelled",
            Confidence.ESTIMATED,
            "Royalty here is rate x list price minus printing cost. It does "
            "not model VAT, delivery, or marketplace-specific deductions, so a "
            "real payout can be lower.",
        ),
    )


def compare(
    prices: list[float],
    page_count: int,
    *,
    marketplace: str = "amazon.com",
    ink: Ink = Ink.BLACK,
    stock: PaperStock = PaperStock.WHITE,
    trim_class: TrimClass | None = None,
    assumptions: tuple[Assumption, ...] | None = None,
) -> Economics:
    """Build a scenario per price so they can be compared side by side.

    No price is privileged: whether $5.99 works depends on the page count and
    on which side of the $9.99 royalty step it falls.

    ``trim_class`` has no default. Omitting it yields an Economics with no
    scenarios and a recorded reason, because KDP's printing cost genuinely
    depends on it and guessing would produce a confident wrong answer.
    ``for_book`` resolves it from a book's trim and paper instead.
    """
    if trim_class is None:
        return Economics(
            assumptions=assumptions
            or default_assumptions(marketplace=marketplace, ink=ink),
            unavailable=(
                "No trim class supplied, so no printing cost could be looked "
                "up. KDP prices regular and large trims differently and this "
                "package does not yet know which sizes are which.",
            ),
        )

    scenarios: list[PricingScenario] = []
    unavailable: list[str] = []
    for price in sorted(prices):
        try:
            scenarios.append(
                build_scenario(
                    price, page_count, marketplace=marketplace, ink=ink,
                    stock=stock, trim_class=trim_class,
                )
            )
        except EconomicsUnavailable as exc:
            unavailable.append(f"${price:.2f}: {exc}")

    return Economics(
        scenarios=tuple(scenarios),
        assumptions=assumptions
        or default_assumptions(marketplace=marketplace, ink=ink, trim_class=trim_class),
        unavailable=tuple(unavailable),
    )


def for_book(
    prices: list[float],
    *,
    trim: object,
    paper: object,
    page_count: int,
    marketplace: str = "amazon.com",
) -> Economics:
    """Price a book from its own trim and paper.

    The single call worth using from the pipeline: it takes the ``TrimSize``
    and ``Paper`` the book already declares and resolves ink, stock and trim
    class from them, so the cost table cannot end up describing a different
    book from the one being built. ``trim`` and ``paper`` are typed loosely
    only to keep this module independent of ``kdp.specs``.
    """
    return compare(
        prices,
        page_count,
        marketplace=marketplace,
        ink=Ink(paper.ink),
        stock=PaperStock(paper.stock),
        trim_class=TrimClass(trim.cost_class),
    )
