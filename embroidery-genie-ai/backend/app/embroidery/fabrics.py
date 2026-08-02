"""Fabric profiles.

Every value is expressed in the units the stitch generators expect
(internal units = 0.1 mm) or as a plain multiplier.  The numbers follow
standard commercial digitizing practice: stable wovens get light underlay and
minimal compensation, stretchy knits and pile fabrics get heavier underlay and
more pull compensation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .units import mm_to_units


@dataclass(frozen=True)
class FabricProfile:
    key: str
    name: str
    category: str

    # Fill density: distance between adjacent fill rows, in mm.
    fill_spacing_mm: float = 0.40
    # Satin density: distance between adjacent satin zigzag legs, in mm.
    satin_spacing_mm: float = 0.35
    # Pull compensation applied perpendicular to the stitch direction, in mm.
    pull_compensation_mm: float = 0.20
    # Underlay recipe.
    underlay: tuple[str, ...] = ("edge_run", "zigzag")
    underlay_inset_mm: float = 0.60
    underlay_spacing_mm: float = 2.5
    # Maximum single stitch length the machine allows, in mm.
    max_stitch_mm: float = 12.0
    min_stitch_mm: float = 0.6
    # Stitch length *within* a tatami fill row, in mm. This is a craft choice,
    # not a machine limit: too long and the fill snags, too short and it boards
    # up and breaks needles.
    fill_stitch_mm: float = 4.0
    # Widest column still sewable as satin, in mm.
    max_satin_width_mm: float = 10.0
    # Machine speed derate: 1.0 = full speed, 0.7 = run at 70%.
    speed_factor: float = 1.0
    # Stabiliser and needle guidance surfaced to the operator.
    stabilizer: str = "1.8 oz cutaway"
    needle: str = "75/11 sharp"
    topping: str = "none"
    notes: str = ""
    # Minimum recommended lettering height in mm on this fabric.
    min_letter_height_mm: float = 5.0
    tags: tuple[str, ...] = field(default_factory=tuple)

    # ------------------------------------------------------- unit conversions
    @property
    def fill_spacing(self) -> float:
        return mm_to_units(self.fill_spacing_mm)

    @property
    def satin_spacing(self) -> float:
        return mm_to_units(self.satin_spacing_mm)

    @property
    def pull_compensation(self) -> float:
        return mm_to_units(self.pull_compensation_mm)

    @property
    def underlay_inset(self) -> float:
        return mm_to_units(self.underlay_inset_mm)

    @property
    def underlay_spacing(self) -> float:
        return mm_to_units(self.underlay_spacing_mm)

    @property
    def max_stitch(self) -> float:
        return mm_to_units(self.max_stitch_mm)

    @property
    def fill_stitch(self) -> float:
        return mm_to_units(self.fill_stitch_mm)

    @property
    def min_stitch(self) -> float:
        return mm_to_units(self.min_stitch_mm)

    @property
    def max_satin_width(self) -> float:
        return mm_to_units(self.max_satin_width_mm)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["underlay"] = list(self.underlay)
        data["tags"] = list(self.tags)
        return data


FABRIC_PROFILES: dict[str, FabricProfile] = {
    p.key: p
    for p in [
        FabricProfile(
            key="cotton_shirt",
            name="Cotton Shirt",
            category="woven",
            fill_spacing_mm=0.40,
            satin_spacing_mm=0.35,
            pull_compensation_mm=0.15,
            underlay=("edge_run",),
            underlay_inset_mm=0.5,
            stabilizer="1.5 oz tearaway",
            needle="75/11 sharp",
            min_letter_height_mm=4.5,
            tags=("apparel", "stable"),
            notes="Stable woven. Light underlay is enough; avoid over-densing.",
        ),
        FabricProfile(
            key="polyester_shirt",
            name="Polyester / Performance Shirt",
            category="knit",
            fill_spacing_mm=0.45,
            satin_spacing_mm=0.38,
            pull_compensation_mm=0.25,
            underlay=("center_run", "edge_run"),
            underlay_inset_mm=0.6,
            max_stitch_mm=10.0,
            speed_factor=0.85,
            stabilizer="1.8 oz cutaway (no-show mesh)",
            needle="75/11 ballpoint",
            min_letter_height_mm=5.0,
            tags=("apparel", "stretch"),
            notes="Stretchy and heat sensitive. Reduce density and slow the head "
            "to avoid puckering and needle burn.",
        ),
        FabricProfile(
            key="hoodie",
            name="Fleece Hoodie",
            category="knit",
            fill_spacing_mm=0.42,
            satin_spacing_mm=0.36,
            pull_compensation_mm=0.30,
            underlay=("center_run", "zigzag", "edge_run"),
            underlay_inset_mm=0.7,
            underlay_spacing_mm=2.0,
            speed_factor=0.85,
            stabilizer="2.5 oz cutaway",
            needle="75/11 ballpoint",
            topping="water soluble film",
            min_letter_height_mm=6.0,
            tags=("apparel", "pile", "stretch"),
            notes="Pile fabric. Water soluble topping keeps stitches from sinking.",
        ),
        FabricProfile(
            key="hat",
            name="Structured Hat / Cap",
            category="cap",
            fill_spacing_mm=0.38,
            satin_spacing_mm=0.35,
            pull_compensation_mm=0.30,
            underlay=("center_run", "zigzag"),
            underlay_inset_mm=0.6,
            underlay_spacing_mm=2.0,
            max_stitch_mm=8.0,
            max_satin_width_mm=8.0,
            speed_factor=0.65,
            stabilizer="fused buckram (built in)",
            needle="75/11 sharp",
            min_letter_height_mm=6.0,
            fill_stitch_mm=3.5,
            tags=("headwear",),
            notes="Sew bottom-up and centre-out on a cap driver. Keep columns "
            "under 8 mm and cap the head at ~700 spm.",
        ),
        FabricProfile(
            key="beanie",
            name="Knit Beanie",
            category="knit",
            fill_spacing_mm=0.45,
            satin_spacing_mm=0.40,
            pull_compensation_mm=0.45,
            underlay=("center_run", "zigzag", "edge_run"),
            underlay_inset_mm=0.8,
            underlay_spacing_mm=1.8,
            speed_factor=0.6,
            stabilizer="2.5 oz cutaway + hoop with cap frame",
            needle="75/11 ballpoint",
            topping="water soluble film",
            min_letter_height_mm=8.0,
            fill_stitch_mm=3.8,
            tags=("headwear", "stretch", "pile"),
            notes="Very unstable. Heavy underlay, generous compensation, large "
            "lettering only.",
        ),
        FabricProfile(
            key="leather_patch",
            name="Leather Patch",
            category="leather",
            fill_spacing_mm=0.50,
            satin_spacing_mm=0.45,
            pull_compensation_mm=0.05,
            underlay=(),
            underlay_inset_mm=0.0,
            max_stitch_mm=10.0,
            speed_factor=0.55,
            stabilizer="none (backed patch)",
            needle="80/12 leather point",
            min_letter_height_mm=5.0,
            fill_stitch_mm=4.5,
            tags=("patch", "rigid"),
            notes="Every penetration is permanent. Minimise underlay and density; "
            "perforation weakens the substrate.",
        ),
        FabricProfile(
            key="canvas",
            name="Canvas / Duck",
            category="woven",
            fill_spacing_mm=0.42,
            satin_spacing_mm=0.36,
            pull_compensation_mm=0.10,
            underlay=("edge_run",),
            underlay_inset_mm=0.5,
            speed_factor=0.8,
            stabilizer="1.5 oz tearaway",
            needle="80/12 sharp",
            min_letter_height_mm=4.5,
            fill_stitch_mm=4.2,
            tags=("bags", "stable", "heavy"),
            notes="Dense weave. Use a sharp needle and slow slightly to protect "
            "the thread.",
        ),
        FabricProfile(
            key="towel",
            name="Terry Towel",
            category="pile",
            fill_spacing_mm=0.38,
            satin_spacing_mm=0.32,
            pull_compensation_mm=0.35,
            underlay=("center_run", "zigzag", "edge_run"),
            underlay_inset_mm=0.8,
            underlay_spacing_mm=1.6,
            speed_factor=0.7,
            stabilizer="1.8 oz cutaway",
            needle="75/11 ballpoint",
            topping="water soluble film (required)",
            min_letter_height_mm=8.0,
            fill_stitch_mm=3.8,
            tags=("home", "pile"),
            notes="High pile. Topping is mandatory or the design disappears.",
        ),
    ]
}

DEFAULT_FABRIC = "cotton_shirt"


def get_fabric(key: str | None) -> FabricProfile:
    return FABRIC_PROFILES.get(key or DEFAULT_FABRIC, FABRIC_PROFILES[DEFAULT_FABRIC])


def list_fabrics() -> list[dict]:
    return [p.to_dict() for p in FABRIC_PROFILES.values()]
