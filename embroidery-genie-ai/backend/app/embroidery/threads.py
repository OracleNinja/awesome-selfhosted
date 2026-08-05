"""Thread catalogues and colour matching.

Ships with a 64-colour house catalogue ("Genie Standard") that covers the
range a production shop actually keeps on the wall.  Real manufacturer charts
(Madeira Polyneon, Isacord, Robison-Anton, ...) are licensed data, so instead
of guessing at their code numbers we support importing a chart from CSV:

    code,name,hex
    1902,Ivory,#F2E8D5

Matching runs in CIE L*a*b* with the CIE76 distance, which tracks perceived
colour difference far better than RGB euclidean distance and is what avoids
"why did it pick the muddy brown" complaints.
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass

from .pattern import Thread


@dataclass(frozen=True)
class CatalogEntry:
    code: str
    name: str
    r: int
    g: int
    b: int

    def to_thread(self, catalog: str) -> Thread:
        return Thread(self.r, self.g, self.b, catalog=catalog, code=self.code, name=self.name)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


_GENIE_STANDARD_RAW: list[tuple[str, str, str]] = [
    ("GS-1000", "Snow White", "#FFFFFF"),
    ("GS-1001", "Soft White", "#F7F4EC"),
    ("GS-1002", "Ivory", "#F2E8D5"),
    ("GS-1003", "Cream", "#EFE0BF"),
    ("GS-1010", "Bone", "#E3D9C6"),
    ("GS-1020", "Light Grey", "#CFD2D3"),
    ("GS-1021", "Silver", "#B4B8BB"),
    ("GS-1022", "Steel Grey", "#8A9095"),
    ("GS-1023", "Charcoal", "#4A4E52"),
    ("GS-1024", "Graphite", "#2E3134"),
    ("GS-1030", "Jet Black", "#0B0B0B"),
    ("GS-1031", "Soft Black", "#1C1C1C"),
    ("GS-2000", "Lemon", "#F6E96B"),
    ("GS-2001", "Sunflower", "#F5C518"),
    ("GS-2002", "Gold", "#D9A521"),
    ("GS-2003", "Old Gold", "#B8860B"),
    ("GS-2010", "Peach", "#F7BFA2"),
    ("GS-2011", "Apricot", "#F0A05A"),
    ("GS-2012", "Tangerine", "#F07818"),
    ("GS-2013", "Pumpkin", "#D95F02"),
    ("GS-2014", "Rust", "#A6431B"),
    ("GS-3000", "Blush", "#F5C6C9"),
    ("GS-3001", "Rose", "#E8798E"),
    ("GS-3002", "Fuchsia", "#D22B72"),
    ("GS-3003", "Magenta", "#B01B64"),
    ("GS-3010", "Coral", "#F2665A"),
    ("GS-3011", "Poppy Red", "#E02B22"),
    ("GS-3012", "True Red", "#C4161C"),
    ("GS-3013", "Cardinal", "#9E1B24"),
    ("GS-3014", "Burgundy", "#6E1120"),
    ("GS-3015", "Maroon", "#4F0F1B"),
    ("GS-4000", "Lilac", "#CDB6E0"),
    ("GS-4001", "Lavender", "#A98BD1"),
    ("GS-4002", "Violet", "#7B4EA8"),
    ("GS-4003", "Purple", "#5B2D82"),
    ("GS-4004", "Plum", "#3F1D5C"),
    ("GS-5000", "Powder Blue", "#BBD9EE"),
    ("GS-5001", "Sky", "#7EBBE3"),
    ("GS-5002", "Azure", "#2E8FD1"),
    ("GS-5003", "Royal Blue", "#1257A6"),
    ("GS-5004", "Navy", "#12305C"),
    ("GS-5005", "Midnight Navy", "#0B1D3A"),
    ("GS-5010", "Aqua", "#66D0D6"),
    ("GS-5011", "Teal", "#0F8C93"),
    ("GS-5012", "Deep Teal", "#0A5C63"),
    ("GS-6000", "Mint", "#B8E3C4"),
    ("GS-6001", "Lime", "#9ACD32"),
    ("GS-6002", "Kelly Green", "#2E9B45"),
    ("GS-6003", "Emerald", "#0F7A46"),
    ("GS-6004", "Forest Green", "#125230"),
    ("GS-6005", "Hunter Green", "#0C3A25"),
    ("GS-6010", "Sage", "#A7B79A"),
    ("GS-6011", "Olive", "#6B7134"),
    ("GS-6012", "Army Green", "#4A4E28"),
    ("GS-7000", "Sand", "#DCC9A6"),
    ("GS-7001", "Khaki", "#C0A87C"),
    ("GS-7002", "Camel", "#A9814F"),
    ("GS-7003", "Chestnut", "#7C4F2C"),
    ("GS-7004", "Chocolate", "#4E2E1E"),
    ("GS-7005", "Espresso", "#2F1C13"),
    ("GS-8000", "Metallic Silver", "#C9CCD1"),
    ("GS-8001", "Metallic Gold", "#C8A951"),
    ("GS-8002", "Metallic Copper", "#B06A3B"),
    ("GS-9000", "Neon Yellow", "#E8FF3A"),
]


# ------------------------------------------------------------ colour science
def _srgb_to_linear(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _rgb_to_xyz(r: int, g: int, b: int) -> tuple[float, float, float]:
    rl, gl, bl = _srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b)
    x = rl * 0.4124564 + gl * 0.3575761 + bl * 0.1804375
    y = rl * 0.2126729 + gl * 0.7151522 + bl * 0.0721750
    z = rl * 0.0193339 + gl * 0.1191920 + bl * 0.9503041
    return (x, y, z)


def _rgb_to_lab(r: int, g: int, b: int) -> tuple[float, float, float]:
    # D65 reference white
    xn, yn, zn = 0.95047, 1.00000, 1.08883
    x, y, z = _rgb_to_xyz(r, g, b)

    def f(t: float) -> float:
        return t ** (1.0 / 3.0) if t > 0.008856 else (7.787 * t) + (16.0 / 116.0)

    fx, fy, fz = f(x / xn), f(y / yn), f(z / zn)
    return (116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz))


class ThreadCatalog:
    def __init__(self, name: str, entries: list[CatalogEntry]):
        self.name = name
        self.entries = entries
        self._lab = [_rgb_to_lab(e.r, e.g, e.b) for e in entries]

    def __len__(self) -> int:
        return len(self.entries)

    def match(self, rgb: tuple[int, int, int]) -> Thread:
        """Nearest catalogue colour to an arbitrary RGB triple."""
        if not self.entries:
            return Thread(*rgb, catalog=self.name)
        target = _rgb_to_lab(*rgb)
        best_index = 0
        best_dist = float("inf")
        for i, lab in enumerate(self._lab):
            d = (
                (lab[0] - target[0]) ** 2
                + (lab[1] - target[1]) ** 2
                + (lab[2] - target[2]) ** 2
            )
            if d < best_dist:
                best_dist, best_index = d, i
        return self.entries[best_index].to_thread(self.name)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "count": len(self.entries),
            "colors": [
                {"code": e.code, "name": e.name, "hex": f"#{e.r:02X}{e.g:02X}{e.b:02X}"}
                for e in self.entries
            ],
        }

    @classmethod
    def from_csv(cls, name: str, text: str) -> "ThreadCatalog":
        """Import a manufacturer chart: ``code,name,hex`` with an optional header."""
        entries: list[CatalogEntry] = []
        reader = csv.reader(io.StringIO(text))
        for row in reader:
            if len(row) < 3:
                continue
            code, label, color = row[0].strip(), row[1].strip(), row[2].strip()
            if code.lower() in {"code", "number", "#"}:
                continue
            try:
                r, g, b = _hex_to_rgb(color)
            except (ValueError, IndexError):
                continue
            entries.append(CatalogEntry(code=code, name=label, r=r, g=g, b=b))
        if not entries:
            raise ValueError("No usable rows found. Expected: code,name,hex")
        return cls(name, entries)


GENIE_STANDARD = ThreadCatalog(
    "Genie Standard",
    [CatalogEntry(code, name, *_hex_to_rgb(hexv)) for code, name, hexv in _GENIE_STANDARD_RAW],
)

CATALOGS: dict[str, ThreadCatalog] = {"genie_standard": GENIE_STANDARD}


def get_catalog(key: str | None) -> ThreadCatalog:
    return CATALOGS.get(key or "genie_standard", GENIE_STANDARD)


def register_catalog(key: str, catalog: ThreadCatalog) -> None:
    CATALOGS[key] = catalog


def color_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """Perceptual (CIE76) distance between two RGB colours."""
    la, lb = _rgb_to_lab(*a), _rgb_to_lab(*b)
    return math.sqrt(sum((la[i] - lb[i]) ** 2 for i in range(3)))


def suggest_thread(rgb: tuple[int, int, int], catalog_key: str | None = None) -> Thread:
    return get_catalog(catalog_key).match(rgb)
