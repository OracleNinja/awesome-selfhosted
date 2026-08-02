"""Export format registry.

Design note on backends
-----------------------
Embroidery formats are undocumented binary blobs reverse-engineered by the
community.  Two of them are stable and simple enough to own outright:

* **DST** (Tajima) — the universal commercial interchange format, and the one
  a shop actually sends to the floor. Implemented natively here.
* **EXP** (Melco) — trivially simple. Implemented natively here.

The rest (PES, JEF, VP3, HUS, XXX) carry version-specific headers, embedded
preview bitmaps and vendor colour tables.  Re-deriving those from memory would
produce files that *look* right and fail on a machine at 2 a.m., so they are
written through `pyembroidery`, a mature MIT-licensed pure-Python
implementation that is a hard dependency of this service.  When it is present
it also handles DST/EXP; the native writers stay as a zero-dependency fallback
and as the reference implementation our tests assert against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..pattern import EmbroideryPattern


@dataclass(frozen=True)
class FormatSpec:
    extension: str
    name: str
    vendor: str
    native: bool          # do we have our own writer?
    notes: str = ""

    @property
    def key(self) -> str:
        return self.extension.lower()


FORMATS: dict[str, FormatSpec] = {
    spec.key: spec
    for spec in [
        FormatSpec("dst", "Tajima DST", "Tajima", True,
                   "Universal commercial format. Carries no colour data - the "
                   "thread chart travels alongside it."),
        FormatSpec("exp", "Melco EXP", "Melco / Bernina", True,
                   "Simple stitch-only format."),
        FormatSpec("pes", "Brother PES", "Brother / Babylock", False,
                   "Home and mid-range commercial Brother machines. Embeds "
                   "colours and a preview."),
        FormatSpec("jef", "Janome JEF", "Janome / Elna", False,
                   "Janome home machines. Embeds the hoop and colour table."),
        FormatSpec("vp3", "Husqvarna VP3", "Husqvarna Viking / Pfaff", False,
                   "Newer Viking and Pfaff machines. Use this instead of HUS."),
        FormatSpec("hus", "Husqvarna HUS", "Husqvarna Viking", False,
                   "Legacy Viking format. Import only — its stitch block uses a "
                   "proprietary compression with no open writer. Export VP3 for "
                   "Viking and Pfaff machines."),
        FormatSpec("xxx", "Singer XXX", "Singer", False,
                   "Singer machines."),
        FormatSpec("u01", "Barudan U01", "Barudan", False,
                   "Barudan commercial heads."),
        FormatSpec("pec", "Brother PEC", "Brother / Babylock", False,
                   "The raw stitch block inside a PES file. Some older Brother "
                   "machines read it directly."),
    ]
}

# Formats we can produce today. Everything else in FORMATS is listed so the UI
# can explain *why* it is unavailable rather than silently omitting it.
EXPORTABLE = {"dst", "exp", "pes", "jef", "vp3", "xxx", "u01", "pec"}


WriterFn = Callable[[EmbroideryPattern], bytes]
_NATIVE_WRITERS: dict[str, WriterFn] = {}


def register_native(extension: str, fn: WriterFn) -> None:
    _NATIVE_WRITERS[extension.lower()] = fn


def native_writer(extension: str) -> WriterFn | None:
    return _NATIVE_WRITERS.get(extension.lower())


def supported_formats() -> list[dict]:
    return [
        {
            "extension": spec.extension,
            "name": spec.name,
            "vendor": spec.vendor,
            "native": spec.native,
            "notes": spec.notes,
        }
        for spec in FORMATS.values()
    ]


class UnsupportedFormatError(ValueError):
    pass


# ------------------------------------------------------------ binary helpers
def int16le(value: int) -> bytes:
    return int(value).to_bytes(2, "little", signed=True)


def uint16le(value: int) -> bytes:
    return int(value).to_bytes(2, "little", signed=False)


def int32le(value: int) -> bytes:
    return int(value).to_bytes(4, "little", signed=True)


def uint32le(value: int) -> bytes:
    return int(value).to_bytes(4, "little", signed=False)


def signed_byte(value: int) -> bytes:
    return int(max(-128, min(127, value))).to_bytes(1, "little", signed=True)
