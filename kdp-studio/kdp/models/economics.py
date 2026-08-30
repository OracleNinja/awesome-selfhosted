"""Book economics.

Every number here is a projection. The model's job is to stay honest about
that: each figure carries a :class:`Confidence`, and a projection built on
assumed inputs is reported as assumed no matter how precise the arithmetic
looks. Simulated revenue is never presented as income.

The KDP royalty formula and printing-cost constants are marked ``ASSUMED``
until the ``royalty`` specification table is verified against KDP's own page.
Until then the economics gate reports the numbers but refuses to certify them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import ModelError
from .strategy import Confidence

#: KDP's paperback royalty rate on most marketplaces, before printing cost.
#: ASSUMED — see kdp/specs/revision.py, table "royalty".
ASSUMED_ROYALTY_RATE: float = 0.60

#: Printing cost model: a fixed charge per book plus a per-page charge.
#: ASSUMED — black ink on white paper, amazon.com, USD.
ASSUMED_FIXED_COST_USD: float = 1.00
ASSUMED_PER_PAGE_COST_USD: float = 0.012


@dataclass(frozen=True, slots=True)
class Assumption:
    """One input to the model, and how well it is known."""

    key: str
    value: float
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
    """One list price and what it would earn, under stated assumptions."""

    label: str
    list_price_usd: float
    page_count: int
    marketplace: str = "amazon.com"
    currency: str = "USD"
    royalty_rate: float = ASSUMED_ROYALTY_RATE
    fixed_cost_usd: float = ASSUMED_FIXED_COST_USD
    per_page_cost_usd: float = ASSUMED_PER_PAGE_COST_USD

    def __post_init__(self) -> None:
        if self.list_price_usd <= 0:
            raise ModelError(f"scenario {self.label!r} has a non-positive list price")
        if self.page_count <= 0:
            raise ModelError(f"scenario {self.label!r} has a non-positive page count")

    @property
    def print_cost_usd(self) -> float:
        return round(
            self.fixed_cost_usd + (self.page_count * self.per_page_cost_usd), 4
        )

    @property
    def gross_royalty_usd(self) -> float:
        return round(self.list_price_usd * self.royalty_rate, 4)

    @property
    def net_royalty_usd(self) -> float:
        """What the author would receive per copy. Can be negative."""
        return round(self.gross_royalty_usd - self.print_cost_usd, 4)

    @property
    def viable(self) -> bool:
        """False when a copy sold would lose money."""
        return self.net_royalty_usd > 0

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "list_price_usd": self.list_price_usd,
            "page_count": self.page_count,
            "marketplace": self.marketplace,
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
                marketplace=data.get("marketplace", "amazon.com"),
                currency=data.get("currency", "USD"),
                royalty_rate=float(data.get("royalty_rate", ASSUMED_ROYALTY_RATE)),
                fixed_cost_usd=float(
                    data.get("fixed_cost_usd", ASSUMED_FIXED_COST_USD)
                ),
                per_page_cost_usd=float(
                    data.get("per_page_cost_usd", ASSUMED_PER_PAGE_COST_USD)
                ),
            )
        except KeyError as exc:
            raise ModelError(f"pricing scenario missing field {exc.args[0]!r}") from None


@dataclass(frozen=True, slots=True)
class Economics:
    """Pricing scenarios for one book, plus the assumptions behind them.

    ``confidence`` is the weakest confidence among the assumptions: a
    projection is only as sound as its shakiest input, and rounding that up
    would be exactly the misrepresentation this model exists to prevent.
    """

    #: The scenario chosen for the listing, by label.
    selected: str | None = None
    scenarios: tuple[PricingScenario, ...] = field(default_factory=tuple)
    assumptions: tuple[Assumption, ...] = field(default_factory=tuple)

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
                    value=float(a["value"]),
                    confidence=Confidence(a.get("confidence", "unknown")),
                    source=a.get("source", ""),
                )
                for a in data.get("assumptions", ())
            ),
        )


def default_assumptions() -> tuple[Assumption, ...]:
    """The built-in model inputs, all ASSUMED until the royalty table is checked."""
    return (
        Assumption(
            "royalty_rate",
            ASSUMED_ROYALTY_RATE,
            Confidence.UNKNOWN,
            "kdp/specs/revision.py table 'royalty' is unverified",
        ),
        Assumption(
            "fixed_cost_usd",
            ASSUMED_FIXED_COST_USD,
            Confidence.UNKNOWN,
            "kdp/specs/revision.py table 'royalty' is unverified",
        ),
        Assumption(
            "per_page_cost_usd",
            ASSUMED_PER_PAGE_COST_USD,
            Confidence.UNKNOWN,
            "kdp/specs/revision.py table 'royalty' is unverified",
        ),
    )


def compare(
    prices: list[float], page_count: int, assumptions: tuple[Assumption, ...] | None = None
) -> Economics:
    """Build a scenario per price so they can be compared side by side.

    No price is privileged: $5.99 is a scenario like any other, and whether it
    is the right one depends on the page count and the printing cost.
    """
    scenarios = tuple(
        PricingScenario(label=f"${price:.2f}", list_price_usd=price, page_count=page_count)
        for price in sorted(prices)
    )
    return Economics(
        scenarios=scenarios, assumptions=assumptions or default_assumptions()
    )
