"""Tajima DST writer.

Format
------
512-byte ASCII header followed by 3-byte stitch records.

Each record encodes a *relative* move in the range +-121 units (0.1 mm) using
a ternary bit layout, plus command bits in the third byte:

    byte 3   0x03  base (always set)
             0x80  jump
             0xC3  colour change / stop
             0xF3  end of file (with a zero move)

DST stores Y increasing *upward*; our internal frame is Y-down (SVG
convention), so the writer negates Y on the way out.  DST carries no colour
information at all — that is why every export package ships a thread chart.
"""

from __future__ import annotations

from ..pattern import Command, EmbroideryPattern
from .base import register_native

MAX_MOVE = 121


def _encode(dx: int, dy: int, command: Command) -> bytes:
    """Encode one relative move.  ``dx``/``dy`` must be within +-121."""
    if command == Command.END:
        return b"\x00\x00\xf3"

    x = int(dx)
    y = int(dy)
    b0 = b1 = b2 = 0

    if command in (Command.JUMP, Command.TRIM):
        b2 = 0x83
    elif command in (Command.COLOR_CHANGE, Command.STOP):
        b2 = 0xC3
    else:
        b2 = 0x03

    if x > 40:
        b2 += 0x04
        x -= 81
    if x < -40:
        b2 += 0x08
        x += 81
    if y > 40:
        b2 += 0x20
        y -= 81
    if y < -40:
        b2 += 0x10
        y += 81

    if x > 13:
        b1 += 0x04
        x -= 27
    if x < -13:
        b1 += 0x08
        x += 27
    if y > 13:
        b1 += 0x20
        y -= 27
    if y < -13:
        b1 += 0x10
        y += 27

    if x > 4:
        b0 += 0x04
        x -= 9
    if x < -4:
        b0 += 0x08
        x += 9
    if y > 4:
        b0 += 0x20
        y -= 9
    if y < -4:
        b0 += 0x10
        y += 9

    if x > 1:
        b1 += 0x01
        x -= 3
    if x < -1:
        b1 += 0x02
        x += 3
    if y > 1:
        b1 += 0x80
        y -= 3
    if y < -1:
        b1 += 0x40
        y += 3

    if x > 0:
        b0 += 0x01
        x -= 1
    if x < 0:
        b0 += 0x02
        x += 1
    if y > 0:
        b0 += 0x80
        y -= 1
    if y < 0:
        b0 += 0x40
        y += 1

    return bytes([b0, b1, b2])


def _header(pattern: EmbroideryPattern, stitch_count: int) -> bytes:
    min_x, min_y, max_x, max_y = pattern.bounds()
    # Header extents are in DST's Y-up frame.
    fields = [
        f"LA:{pattern.name[:16]:<16}\r",
        f"ST:{stitch_count:7d}\r",
        f"CO:{max(1, pattern.color_count) - 1:3d}\r",
        f"+X:{abs(int(max_x)):5d}\r",
        f"-X:{abs(int(min_x)):5d}\r",
        f"+Y:{abs(int(-min_y)):5d}\r",
        f"-Y:{abs(int(-max_y)):5d}\r",
        f"AX:+{0:5d}\r",
        f"AY:+{0:5d}\r",
        f"MX:+{0:5d}\r",
        f"MY:+{0:5d}\r",
        "PD:******\r",
    ]
    header = "".join(fields).encode("ascii", errors="replace") + b"\x1a"
    if len(header) > 512:
        header = header[:512]
    return header + b"\x20" * (512 - len(header))


def _emit_move(out: bytearray, dx: int, dy: int, command: Command) -> None:
    """Split an over-long move into multiple jump records."""
    while abs(dx) > MAX_MOVE or abs(dy) > MAX_MOVE:
        step_x = max(-MAX_MOVE, min(MAX_MOVE, dx))
        step_y = max(-MAX_MOVE, min(MAX_MOVE, dy))
        out += _encode(step_x, step_y, Command.JUMP)
        dx -= step_x
        dy -= step_y
    out += _encode(dx, dy, command)


def write_dst(pattern: EmbroideryPattern) -> bytes:
    body = bytearray()
    prev_x = 0.0
    prev_y = 0.0
    emitted = 0

    for block_index, block in enumerate(pattern.blocks):
        if block_index > 0:
            # DST signals a change by a zero-move colour-change record.
            body += _encode(0, 0, Command.COLOR_CHANGE)
        for stitch in block.stitches:
            # Round against the running position so error never accumulates,
            # and negate Y because DST counts upward.
            dx = int(round(stitch.x - prev_x))
            dy = int(round(prev_y - stitch.y))
            if dx == 0 and dy == 0 and stitch.command == Command.STITCH:
                continue
            command = stitch.command
            if command == Command.TRIM:
                command = Command.JUMP
            _emit_move(body, dx, dy, command)
            prev_x += dx
            prev_y -= dy
            if command == Command.STITCH:
                emitted += 1

    body += _encode(0, 0, Command.END)
    return _header(pattern, emitted) + bytes(body)


register_native("dst", write_dst)
