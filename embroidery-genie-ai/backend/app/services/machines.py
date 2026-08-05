"""Module 5 — Machine profile presets.

Starting points for the machines a shop is most likely to own.  Users add
their own; these exist so the first-run experience is not an empty form.
Hoop sizes are the maximum sewing field of the machine's largest standard
hoop, which is the number that matters for a pre-flight check.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class MachinePreset:
    key: str
    brand: str
    model: str
    category: str            # commercial | home | cap
    heads: int
    needle_count: int
    hoop_width_mm: float
    hoop_height_mm: float
    max_speed_spm: int
    max_stitch_count: int
    formats: tuple[str, ...]
    notes: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["formats"] = list(self.formats)
        return data


PRESETS: list[MachinePreset] = [
    MachinePreset("tajima_tmbp", "Tajima", "TMBP-S1501", "commercial", 1, 15,
                  500, 360, 1200, 500_000, ("dst", "exp", "u01"),
                  "Single-head 15-needle workhorse. DST is its native format."),
    MachinePreset("tajima_tmar", "Tajima", "TMAR-KC (multi-head)", "commercial", 6, 15,
                  450, 360, 1000, 500_000, ("dst", "exp", "u01"),
                  "Six-head production line. Divide sew time by the head count."),
    MachinePreset("barudan_bevt", "Barudan", "BEVT-S1501CB", "commercial", 1, 15,
                  500, 400, 1200, 500_000, ("u01", "dst", "exp"),
                  "Runs U01 natively; DST is accepted."),
    MachinePreset("ricoma_em1010", "Ricoma", "EM-1010", "home", 1, 10,
                  280, 300, 1000, 200_000, ("dst", "pes", "jef", "exp"),
                  "Popular entry commercial machine."),
    MachinePreset("ricoma_mt1502", "Ricoma", "MT-1502", "commercial", 1, 15,
                  500, 350, 1200, 300_000, ("dst", "pes", "exp"),
                  "15-needle with a cap driver."),
    MachinePreset("melco_emt16x", "Melco", "EMT16X", "commercial", 1, 16,
                  400, 400, 1500, 500_000, ("exp", "dst", "ofm"),
                  "Modular; EXP is native. Very high top speed."),
    MachinePreset("brother_pr1055x", "Brother", "PR1055X", "commercial", 1, 10,
                  360, 200, 1000, 200_000, ("pes", "dst", "pec"),
                  "PES native. Watch the 200 mm hoop height."),
    MachinePreset("brother_se700", "Brother", "SE700", "home", 1, 1,
                  100, 100, 850, 100_000, ("pes", "pec"),
                  "Single needle: every colour change is manual."),
    MachinePreset("janome_mb7", "Janome", "MB-7", "home", 1, 7,
                  230, 200, 800, 100_000, ("jef", "dst"),
                  "JEF native."),
    MachinePreset("janome_500e", "Janome", "Memory Craft 500E", "home", 1, 1,
                  280, 200, 860, 100_000, ("jef", "dst"),
                  "Single needle with a large field."),
    MachinePreset("husqvarna_epic", "Husqvarna Viking", "Designer Epic 3", "home", 1, 1,
                  360, 360, 1050, 120_000, ("vp3", "dst", "pes"),
                  "Export VP3 — HUS is a legacy read-only format."),
    MachinePreset("singer_legacy", "Singer", "Legacy SE300", "home", 1, 1,
                  260, 150, 800, 100_000, ("xxx", "dst", "pes"),
                  "XXX native."),
    MachinePreset("generic_cap", "Generic", "Cap driver station", "cap", 1, 15,
                  360, 75, 700, 200_000, ("dst", "exp"),
                  "Cap frames restrict the sewing field height severely."),
]


def list_presets() -> list[dict]:
    return [preset.to_dict() for preset in PRESETS]


def get_preset(key: str) -> MachinePreset | None:
    return next((p for p in PRESETS if p.key == key), None)


def brands() -> list[str]:
    seen: list[str] = []
    for preset in PRESETS:
        if preset.brand not in seen:
            seen.append(preset.brand)
    return seen
