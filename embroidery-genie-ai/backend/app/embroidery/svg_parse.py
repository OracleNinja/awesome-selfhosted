"""SVG -> stitchable geometry.

A focused parser: everything an embroidery pipeline needs (shapes, paths,
transforms, fills, strokes, groups) and nothing it does not (filters,
gradients as gradients, animation, foreign objects).  Gradients and patterns
collapse to their average colour because thread cannot blend anyway.

Output is a list of :class:`SvgShape` in internal units (0.1 mm), Y-down.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from xml.etree import ElementTree

from .geometry import Point, Ring, dedupe
from .units import parse_length, px_to_units

SVG_NS = "http://www.w3.org/2000/svg"
_TAG_RE = re.compile(r"\{.*\}")

NAMED_COLORS = {
    "black": (0, 0, 0), "white": (255, 255, 255), "red": (255, 0, 0),
    "green": (0, 128, 0), "lime": (0, 255, 0), "blue": (0, 0, 255),
    "yellow": (255, 255, 0), "cyan": (0, 255, 255), "aqua": (0, 255, 255),
    "magenta": (255, 0, 255), "fuchsia": (255, 0, 255), "gray": (128, 128, 128),
    "grey": (128, 128, 128), "silver": (192, 192, 192), "maroon": (128, 0, 0),
    "olive": (128, 128, 0), "navy": (0, 0, 128), "teal": (0, 128, 128),
    "purple": (128, 0, 128), "orange": (255, 165, 0), "pink": (255, 192, 203),
    "brown": (165, 42, 42), "gold": (255, 215, 0), "beige": (245, 245, 220),
}


@dataclass
class SvgShape:
    """One resolved drawing operation."""

    rings: list[Ring]                       # first ring is the outline, rest are holes
    fill: tuple[int, int, int] | None
    stroke: tuple[int, int, int] | None
    stroke_width: float = 0.0               # internal units
    closed: bool = True
    label: str = ""
    kind: str = "path"

    @property
    def outer(self) -> Ring:
        return self.rings[0] if self.rings else []

    @property
    def holes(self) -> list[Ring]:
        return self.rings[1:] if len(self.rings) > 1 else []


# ---------------------------------------------------------------- transforms
Matrix = tuple[float, float, float, float, float, float]  # a b c d e f
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mat_multiply(m1: Matrix, m2: Matrix) -> Matrix:
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def mat_apply(m: Matrix, p: Point) -> Point:
    a, b, c, d, e, f = m
    return (a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f)


def mat_scale_factor(m: Matrix) -> float:
    a, b, c, d, _, _ = m
    return math.sqrt(abs(a * d - b * c)) or 1.0


_TRANSFORM_RE = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")


def parse_transform(text: str | None) -> Matrix:
    if not text:
        return IDENTITY
    result = IDENTITY
    for name, args in _TRANSFORM_RE.findall(text):
        values = [float(v) for v in re.split(r"[\s,]+", args.strip()) if v]
        if name == "matrix" and len(values) == 6:
            m = tuple(values)  # type: ignore[assignment]
        elif name == "translate":
            tx = values[0] if values else 0.0
            ty = values[1] if len(values) > 1 else 0.0
            m = (1.0, 0.0, 0.0, 1.0, tx, ty)
        elif name == "scale":
            sx = values[0] if values else 1.0
            sy = values[1] if len(values) > 1 else sx
            m = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate":
            angle = math.radians(values[0] if values else 0.0)
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            m = (cos_a, sin_a, -sin_a, cos_a, 0.0, 0.0)
            if len(values) >= 3:
                cx, cy = values[1], values[2]
                m = mat_multiply(
                    mat_multiply((1.0, 0.0, 0.0, 1.0, cx, cy), m),
                    (1.0, 0.0, 0.0, 1.0, -cx, -cy),
                )
        elif name == "skewX":
            m = (1.0, 0.0, math.tan(math.radians(values[0] if values else 0.0)), 1.0, 0.0, 0.0)
        elif name == "skewY":
            m = (1.0, math.tan(math.radians(values[0] if values else 0.0)), 0.0, 1.0, 0.0, 0.0)
        else:
            continue
        result = mat_multiply(result, m)  # type: ignore[arg-type]
    return result


# -------------------------------------------------------------------- colour
def parse_color(value: str | None) -> tuple[int, int, int] | None:
    if not value:
        return None
    text = value.strip().lower()
    if text in ("none", "transparent", "inherit"):
        return None
    if text.startswith("url("):
        # Gradients/patterns cannot be stitched; caller substitutes a colour.
        return None
    if text.startswith("#"):
        hexv = text[1:]
        if len(hexv) == 3:
            hexv = "".join(ch * 2 for ch in hexv)
        if len(hexv) >= 6:
            try:
                return (int(hexv[0:2], 16), int(hexv[2:4], 16), int(hexv[4:6], 16))
            except ValueError:
                return None
        return None
    if text.startswith("rgb"):
        nums = re.findall(r"[\d.]+%?", text)
        if len(nums) >= 3:
            out = []
            for n in nums[:3]:
                out.append(int(float(n[:-1]) * 2.55) if n.endswith("%") else int(float(n)))
            return (max(0, min(255, out[0])), max(0, min(255, out[1])), max(0, min(255, out[2])))
        return None
    return NAMED_COLORS.get(text)


def _style_dict(text: str | None) -> dict[str, str]:
    if not text:
        return {}
    out: dict[str, str] = {}
    for chunk in text.split(";"):
        if ":" in chunk:
            key, _, value = chunk.partition(":")
            out[key.strip().lower()] = value.strip()
    return out


# ---------------------------------------------------------------- path parser
_PATH_TOKEN_RE = re.compile(r"([MmZzLlHhVvCcSsQqTtAa])|(-?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)")


def _tokenize_path(d: str) -> list:
    tokens: list = []
    for cmd, num in _PATH_TOKEN_RE.findall(d):
        tokens.append(cmd if cmd else float(num))
    return tokens


def _flatten_cubic(p0: Point, p1: Point, p2: Point, p3: Point, steps: int) -> list[Point]:
    out: list[Point] = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1 - t
        x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
        out.append((x, y))
    return out


def _flatten_quadratic(p0: Point, p1: Point, p2: Point, steps: int) -> list[Point]:
    out: list[Point] = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1 - t
        x = mt**2 * p0[0] + 2 * mt * t * p1[0] + t**2 * p2[0]
        y = mt**2 * p0[1] + 2 * mt * t * p1[1] + t**2 * p2[1]
        out.append((x, y))
    return out


def _curve_steps(p0: Point, p3: Point, resolution: float) -> int:
    span = math.hypot(p3[0] - p0[0], p3[1] - p0[1])
    return max(4, min(64, int(span / max(resolution, 0.5)) + 4))


def _flatten_arc(
    start: Point, rx: float, ry: float, rotation: float,
    large_arc: bool, sweep: bool, end: Point, resolution: float,
) -> list[Point]:
    """Endpoint -> centre parameterisation, per the SVG 1.1 implementation notes."""
    if rx == 0 or ry == 0 or start == end:
        return [end]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(rotation)
    cos_p, sin_p = math.cos(phi), math.sin(phi)

    dx2 = (start[0] - end[0]) / 2.0
    dy2 = (start[1] - end[1]) / 2.0
    x1 = cos_p * dx2 + sin_p * dy2
    y1 = -sin_p * dx2 + cos_p * dy2

    lam = (x1 * x1) / (rx * rx) + (y1 * y1) / (ry * ry)
    if lam > 1:
        scale = math.sqrt(lam)
        rx, ry = rx * scale, ry * scale

    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    factor = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large_arc == sweep:
        factor = -factor
    cxp = factor * rx * y1 / ry
    cyp = -factor * ry * x1 / rx
    cx = cos_p * cxp - sin_p * cyp + (start[0] + end[0]) / 2.0
    cy = sin_p * cxp + cos_p * cyp + (start[1] + end[1]) / 2.0

    def angle_of(ux: float, uy: float) -> float:
        return math.atan2(uy, ux)

    theta1 = angle_of((x1 - cxp) / rx, (y1 - cyp) / ry)
    theta2 = angle_of((-x1 - cxp) / rx, (-y1 - cyp) / ry)
    delta = theta2 - theta1
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi

    steps = max(6, min(96, int(abs(delta) * max(rx, ry) / max(resolution, 0.5)) + 6))
    out: list[Point] = []
    for i in range(1, steps + 1):
        theta = theta1 + delta * (i / steps)
        px = cos_p * rx * math.cos(theta) - sin_p * ry * math.sin(theta) + cx
        py = sin_p * rx * math.cos(theta) + cos_p * ry * math.sin(theta) + cy
        out.append((px, py))
    return out


def parse_path_data(d: str, resolution: float = 1.0) -> list[tuple[list[Point], bool]]:
    """Flatten path data into ``(points, closed)`` subpaths in user space."""
    tokens = _tokenize_path(d)
    subpaths: list[tuple[list[Point], bool]] = []
    current: list[Point] = []
    pos: Point = (0.0, 0.0)
    start: Point = (0.0, 0.0)
    last_cubic_ctrl: Point | None = None
    last_quad_ctrl: Point | None = None
    command = ""
    i = 0

    def flush(closed: bool) -> None:
        nonlocal current
        if len(current) > 1:
            subpaths.append((current, closed))
        current = []

    while i < len(tokens):
        token = tokens[i]
        if isinstance(token, str):
            command = token
            i += 1
            if command in "Zz":
                if current:
                    flush(True)
                pos = start
                continue
        elif not command:
            i += 1
            continue

        relative = command.islower()
        cmd = command.upper()

        def take(n: int) -> list[float]:
            nonlocal i
            values = [float(v) for v in tokens[i : i + n]]
            i += n
            return values

        if cmd == "M":
            x, y = take(2)
            if relative:
                x, y = pos[0] + x, pos[1] + y
            if current:
                flush(False)
            pos = start = (x, y)
            current = [pos]
            command = "l" if relative else "L"
        elif cmd == "L":
            x, y = take(2)
            if relative:
                x, y = pos[0] + x, pos[1] + y
            pos = (x, y)
            current.append(pos)
        elif cmd == "H":
            (x,) = take(1)
            pos = (pos[0] + x if relative else x, pos[1])
            current.append(pos)
        elif cmd == "V":
            (y,) = take(1)
            pos = (pos[0], pos[1] + y if relative else y)
            current.append(pos)
        elif cmd in ("C", "S"):
            if cmd == "C":
                x1, y1, x2, y2, x, y = take(6)
                if relative:
                    x1, y1 = pos[0] + x1, pos[1] + y1
                    x2, y2 = pos[0] + x2, pos[1] + y2
                    x, y = pos[0] + x, pos[1] + y
            else:
                x2, y2, x, y = take(4)
                if relative:
                    x2, y2 = pos[0] + x2, pos[1] + y2
                    x, y = pos[0] + x, pos[1] + y
                if last_cubic_ctrl is not None:
                    x1 = 2 * pos[0] - last_cubic_ctrl[0]
                    y1 = 2 * pos[1] - last_cubic_ctrl[1]
                else:
                    x1, y1 = pos
            steps = _curve_steps(pos, (x, y), resolution)
            current.extend(_flatten_cubic(pos, (x1, y1), (x2, y2), (x, y), steps))
            last_cubic_ctrl = (x2, y2)
            last_quad_ctrl = None
            pos = (x, y)
            continue
        elif cmd in ("Q", "T"):
            if cmd == "Q":
                x1, y1, x, y = take(4)
                if relative:
                    x1, y1 = pos[0] + x1, pos[1] + y1
                    x, y = pos[0] + x, pos[1] + y
            else:
                x, y = take(2)
                if relative:
                    x, y = pos[0] + x, pos[1] + y
                if last_quad_ctrl is not None:
                    x1 = 2 * pos[0] - last_quad_ctrl[0]
                    y1 = 2 * pos[1] - last_quad_ctrl[1]
                else:
                    x1, y1 = pos
            steps = _curve_steps(pos, (x, y), resolution)
            current.extend(_flatten_quadratic(pos, (x1, y1), (x, y), steps))
            last_quad_ctrl = (x1, y1)
            last_cubic_ctrl = None
            pos = (x, y)
            continue
        elif cmd == "A":
            rx, ry, rot, large, sweep, x, y = take(7)
            if relative:
                x, y = pos[0] + x, pos[1] + y
            current.extend(
                _flatten_arc(pos, rx, ry, rot, bool(large), bool(sweep), (x, y), resolution)
            )
            pos = (x, y)
        else:
            i += 1
            continue

        if cmd not in ("C", "S", "Q", "T"):
            last_cubic_ctrl = last_quad_ctrl = None

    if current:
        flush(False)
    return subpaths


# ------------------------------------------------------------------- elements
def _local(tag: str) -> str:
    return _TAG_RE.sub("", tag)


def _points_attr(text: str | None) -> list[Point]:
    if not text:
        return []
    nums = [float(v) for v in re.split(r"[\s,]+", text.strip()) if v]
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]


def _ellipse_ring(cx: float, cy: float, rx: float, ry: float, steps: int = 64) -> list[Point]:
    return [
        (cx + rx * math.cos(2 * math.pi * i / steps), cy + ry * math.sin(2 * math.pi * i / steps))
        for i in range(steps)
    ]


def _rect_ring(x: float, y: float, w: float, h: float, rx: float, ry: float) -> list[Point]:
    if rx <= 0 and ry <= 0:
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    rx = min(rx or ry, w / 2.0)
    ry = min(ry or rx, h / 2.0)
    steps = 12
    pts: list[Point] = []
    corners = [
        (x + w - rx, y + ry, -math.pi / 2, 0.0),
        (x + w - rx, y + h - ry, 0.0, math.pi / 2),
        (x + rx, y + h - ry, math.pi / 2, math.pi),
        (x + rx, y + ry, math.pi, 3 * math.pi / 2),
    ]
    for cx, cy, a0, a1 in corners:
        for i in range(steps + 1):
            t = a0 + (a1 - a0) * i / steps
            pts.append((cx + rx * math.cos(t), cy + ry * math.sin(t)))
    return pts


@dataclass
class SvgDocument:
    shapes: list[SvgShape] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0
    view_box: tuple[float, float, float, float] | None = None


def parse_svg(source: str | bytes, dpi: float = 96.0, curve_resolution: float = 1.0) -> SvgDocument:
    """Parse an SVG document into stitchable shapes (internal units, Y-down)."""
    if isinstance(source, bytes):
        source = source.decode("utf-8", errors="replace")
    root = ElementTree.fromstring(source)

    width_attr = root.get("width")
    height_attr = root.get("height")
    view_box = None
    if root.get("viewBox"):
        parts = [float(v) for v in re.split(r"[\s,]+", root.get("viewBox").strip()) if v]
        if len(parts) == 4:
            view_box = tuple(parts)  # type: ignore[assignment]

    doc_width = parse_length(width_attr, dpi) if width_attr else 0.0
    doc_height = parse_length(height_attr, dpi) if height_attr else 0.0

    # User units -> internal units, honouring the viewBox mapping.
    base_scale = px_to_units(1.0, dpi)
    root_matrix: Matrix = (base_scale, 0.0, 0.0, base_scale, 0.0, 0.0)
    if view_box:
        vb_x, vb_y, vb_w, vb_h = view_box
        if vb_w > 0 and vb_h > 0 and doc_width > 0 and doc_height > 0:
            sx, sy = doc_width / vb_w, doc_height / vb_h
            scale = min(sx, sy)
            root_matrix = (scale, 0.0, 0.0, scale, -vb_x * scale, -vb_y * scale)
        if doc_width <= 0:
            doc_width = px_to_units(vb_w, dpi)
            doc_height = px_to_units(vb_h, dpi)

    shapes: list[SvgShape] = []

    def walk(element, matrix: Matrix, inherited: dict[str, str]) -> None:
        tag = _local(element.tag)
        if tag in ("defs", "metadata", "title", "desc", "clipPath", "mask", "filter"):
            return

        matrix = mat_multiply(matrix, parse_transform(element.get("transform")))
        style = dict(inherited)
        style.update({k: v for k, v in element.attrib.items() if k in
                      ("fill", "stroke", "stroke-width", "fill-rule", "opacity", "display")})
        style.update(_style_dict(element.get("style")))

        if style.get("display") == "none":
            return

        if tag in ("g", "svg", "a", "switch"):
            for child in element:
                walk(child, matrix, style)
            return

        scale = mat_scale_factor(matrix)
        rings: list[Ring] = []
        closed = True
        kind = tag

        if tag == "path":
            d = element.get("d")
            if not d:
                return
            subpaths = parse_path_data(d, curve_resolution / max(scale, 1e-6))
            if not subpaths:
                return
            closed = any(c for _, c in subpaths)
            rings = [[mat_apply(matrix, p) for p in pts] for pts, _ in subpaths]
        elif tag == "rect":
            x = float(element.get("x", 0) or 0)
            y = float(element.get("y", 0) or 0)
            w = float(element.get("width", 0) or 0)
            h = float(element.get("height", 0) or 0)
            if w <= 0 or h <= 0:
                return
            rx = float(element.get("rx", 0) or 0)
            ry = float(element.get("ry", 0) or 0)
            rings = [[mat_apply(matrix, p) for p in _rect_ring(x, y, w, h, rx, ry)]]
        elif tag in ("circle", "ellipse"):
            cx = float(element.get("cx", 0) or 0)
            cy = float(element.get("cy", 0) or 0)
            if tag == "circle":
                r = float(element.get("r", 0) or 0)
                rx = ry = r
            else:
                rx = float(element.get("rx", 0) or 0)
                ry = float(element.get("ry", 0) or 0)
            if rx <= 0 or ry <= 0:
                return
            rings = [[mat_apply(matrix, p) for p in _ellipse_ring(cx, cy, rx, ry)]]
        elif tag in ("polygon", "polyline"):
            pts = _points_attr(element.get("points"))
            if len(pts) < 2:
                return
            closed = tag == "polygon"
            rings = [[mat_apply(matrix, p) for p in pts]]
        elif tag == "line":
            p1 = (float(element.get("x1", 0) or 0), float(element.get("y1", 0) or 0))
            p2 = (float(element.get("x2", 0) or 0), float(element.get("y2", 0) or 0))
            closed = False
            rings = [[mat_apply(matrix, p1), mat_apply(matrix, p2)]]
        elif tag == "text":
            # Text must be converted to outlines upstream; a bare <text> node
            # carries no geometry we can stitch.
            return
        else:
            return

        rings = [dedupe(r, 0.2) for r in rings]
        rings = [r for r in rings if len(r) >= 2]
        if not rings:
            return

        fill = parse_color(style.get("fill", "#000000"))
        if "fill" not in style:
            fill = (0, 0, 0)
        stroke = parse_color(style.get("stroke"))
        stroke_width = 0.0
        if stroke is not None:
            raw = style.get("stroke-width", "1")
            try:
                stroke_width = float(re.sub(r"[a-z%]+$", "", str(raw).strip()) or 1.0) * scale
            except ValueError:
                stroke_width = scale

        shapes.append(
            SvgShape(
                rings=rings,
                fill=fill,
                stroke=stroke,
                stroke_width=stroke_width,
                closed=closed,
                label=element.get("id", "") or element.get("class", ""),
                kind=kind,
            )
        )

    walk(root, root_matrix, {})

    if doc_width <= 0 or doc_height <= 0:
        all_points = [p for s in shapes for r in s.rings for p in r]
        if all_points:
            doc_width = max(p[0] for p in all_points)
            doc_height = max(p[1] for p in all_points)

    return SvgDocument(shapes=shapes, width=doc_width, height=doc_height, view_box=view_box)
