"""In-memory embroidery pattern model.

This is the format-neutral representation that the digitizing engine produces
and every writer consumes.  It is deliberately close to the machine model:
an ordered list of absolute stitch positions carrying a command flag, grouped
into colour blocks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Iterable, Iterator, Sequence

from .units import units_to_mm


class Command(IntEnum):
    """Machine commands attached to a stitch record."""

    STITCH = 0      # normal needle penetration
    JUMP = 1        # frame move without penetration
    TRIM = 2        # cut the thread (implies a jump)
    COLOR_CHANGE = 3
    STOP = 4        # applique / manual stop
    SEQUIN_EJECT = 5
    END = 6


@dataclass(slots=True)
class Stitch:
    x: float
    y: float
    command: Command = Command.STITCH

    def as_tuple(self) -> tuple[float, float, int]:
        return (self.x, self.y, int(self.command))


@dataclass(slots=True)
class Thread:
    """A thread colour, optionally resolved against a manufacturer catalogue."""

    r: int
    g: int
    b: int
    catalog: str = "Generic"
    code: str = ""
    name: str = ""

    @property
    def hex(self) -> str:
        return f"#{self.r:02X}{self.g:02X}{self.b:02X}"

    @property
    def rgb(self) -> tuple[int, int, int]:
        return (self.r, self.g, self.b)

    @classmethod
    def from_hex(cls, value: str, **kwargs) -> "Thread":
        text = value.lstrip("#")
        if len(text) == 3:
            text = "".join(ch * 2 for ch in text)
        return cls(int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), **kwargs)


@dataclass(slots=True)
class StitchBlock:
    """A run of stitches sewn with a single needle / thread."""

    thread: Thread
    stitches: list[Stitch] = field(default_factory=list)
    label: str = ""
    technique: str = "fill"   # fill | satin | running | underlay

    def add(self, x: float, y: float, command: Command = Command.STITCH) -> None:
        self.stitches.append(Stitch(x, y, command))

    def extend(self, points: Iterable[Sequence[float]], command: Command = Command.STITCH) -> None:
        for point in points:
            self.stitches.append(Stitch(float(point[0]), float(point[1]), command))

    @property
    def stitch_count(self) -> int:
        return sum(1 for s in self.stitches if s.command == Command.STITCH)


@dataclass
class EmbroideryPattern:
    """Complete design: ordered colour blocks plus metadata."""

    blocks: list[StitchBlock] = field(default_factory=list)
    name: str = "design"
    metadata: dict = field(default_factory=dict)

    # ---------------------------------------------------------------- blocks
    def new_block(self, thread: Thread, label: str = "", technique: str = "fill") -> StitchBlock:
        block = StitchBlock(thread=thread, label=label, technique=technique)
        self.blocks.append(block)
        return block

    def iter_stitches(self) -> Iterator[tuple[Stitch, StitchBlock]]:
        for block in self.blocks:
            for stitch in block.stitches:
                yield stitch, block

    # ------------------------------------------------------------ statistics
    @property
    def stitch_count(self) -> int:
        return sum(block.stitch_count for block in self.blocks)

    @property
    def jump_count(self) -> int:
        return sum(
            1
            for block in self.blocks
            for s in block.stitches
            if s.command == Command.JUMP
        )

    @property
    def trim_count(self) -> int:
        return sum(
            1
            for block in self.blocks
            for s in block.stitches
            if s.command == Command.TRIM
        )

    @property
    def color_count(self) -> int:
        return len(self.blocks)

    def bounds(self) -> tuple[float, float, float, float]:
        """(min_x, min_y, max_x, max_y) in internal units."""
        xs: list[float] = []
        ys: list[float] = []
        for block in self.blocks:
            for s in block.stitches:
                xs.append(s.x)
                ys.append(s.y)
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))

    def size_mm(self) -> tuple[float, float]:
        min_x, min_y, max_x, max_y = self.bounds()
        return (units_to_mm(max_x - min_x), units_to_mm(max_y - min_y))

    def thread_length_mm(self) -> float:
        """Approximate consumed thread: stitch path length plus a 3x factor for
        the loop taken under the fabric by the bobbin interaction."""
        total = 0.0
        prev: Stitch | None = None
        for block in self.blocks:
            for s in block.stitches:
                if prev is not None and s.command in (Command.STITCH, Command.JUMP):
                    total += math.hypot(s.x - prev.x, s.y - prev.y)
                prev = s
        return units_to_mm(total) * 1.6

    # ------------------------------------------------------------ transforms
    def translate(self, dx: float, dy: float) -> None:
        for block in self.blocks:
            for s in block.stitches:
                s.x += dx
                s.y += dy

    def center_on_origin(self) -> None:
        min_x, min_y, max_x, max_y = self.bounds()
        self.translate(-(min_x + max_x) / 2.0, -(min_y + max_y) / 2.0)

    def scale(self, factor: float) -> None:
        for block in self.blocks:
            for s in block.stitches:
                s.x *= factor
                s.y *= factor

    def flip_y(self) -> None:
        """Most formats use Y-up; raster/SVG sources are Y-down."""
        for block in self.blocks:
            for s in block.stitches:
                s.y = -s.y

    # --------------------------------------------------------------- exports
    def to_flat(self) -> list[tuple[float, float, int]]:
        """Flatten to a single command stream with colour changes inserted."""
        out: list[tuple[float, float, int]] = []
        for index, block in enumerate(self.blocks):
            if index > 0 and block.stitches:
                first = block.stitches[0]
                out.append((first.x, first.y, int(Command.COLOR_CHANGE)))
            for s in block.stitches:
                out.append(s.as_tuple())
        if out:
            last = out[-1]
            out.append((last[0], last[1], int(Command.END)))
        return out

    def summary(self) -> dict:
        width_mm, height_mm = self.size_mm()
        return {
            "name": self.name,
            "stitch_count": self.stitch_count,
            "jump_count": self.jump_count,
            "trim_count": self.trim_count,
            "color_count": self.color_count,
            "width_mm": round(width_mm, 2),
            "height_mm": round(height_mm, 2),
            "thread_length_m": round(self.thread_length_mm() / 1000.0, 2),
            "colors": [
                {
                    "index": i,
                    "hex": b.thread.hex,
                    "name": b.thread.name or b.thread.hex,
                    "code": b.thread.code,
                    "catalog": b.thread.catalog,
                    "stitches": b.stitch_count,
                    "technique": b.technique,
                    "label": b.label,
                }
                for i, b in enumerate(self.blocks)
            ],
        }
