"""Melco EXP writer.

The simplest of the commercial formats: signed byte deltas, with 0x80 as an
escape byte for commands.

    0x80 0x01   colour change
    0x80 0x02   jump (followed by the delta pair)
    0x80 0x04   trim
    dx dy       normal stitch

Like DST it is Y-up and carries no colour table.
"""

from __future__ import annotations

from ..pattern import Command, EmbroideryPattern
from .base import register_native, signed_byte

MAX_MOVE = 127


def _emit(out: bytearray, dx: int, dy: int, escape: bytes | None) -> None:
    while abs(dx) > MAX_MOVE or abs(dy) > MAX_MOVE:
        step_x = max(-MAX_MOVE, min(MAX_MOVE, dx))
        step_y = max(-MAX_MOVE, min(MAX_MOVE, dy))
        out += b"\x80\x02" + signed_byte(step_x) + signed_byte(step_y)
        dx -= step_x
        dy -= step_y
    if escape:
        out += escape
    out += signed_byte(dx) + signed_byte(dy)


def write_exp(pattern: EmbroideryPattern) -> bytes:
    out = bytearray()
    prev_x = 0.0
    prev_y = 0.0

    for block_index, block in enumerate(pattern.blocks):
        if block_index > 0:
            out += b"\x80\x01\x00\x00"
        for stitch in block.stitches:
            dx = int(round(stitch.x - prev_x))
            dy = int(round(prev_y - stitch.y))
            if dx == 0 and dy == 0 and stitch.command == Command.STITCH:
                continue
            if stitch.command == Command.JUMP:
                escape = b"\x80\x02"
            elif stitch.command == Command.TRIM:
                escape = b"\x80\x04"
            elif stitch.command in (Command.COLOR_CHANGE, Command.STOP):
                escape = b"\x80\x01"
            else:
                escape = None
            _emit(out, dx, dy, escape)
            prev_x += dx
            prev_y -= dy

    return bytes(out)


register_native("exp", write_exp)
