"""Raster -> vector tracing.

Pipeline for a PNG/JPG/HEIC/BMP upload:

1. **Load & normalise** — orient, downscale to a sane working resolution.
2. **Background removal** — alpha channel if present, otherwise a flood fill
   from the corners with a colour tolerance.
3. **Colour reduction** — k-means in L*a*b* space so the clusters follow
   perception, not RGB arithmetic. Thread comes in discrete colours; this is
   the step that decides how many needles the job needs.
4. **Cleanup** — morphological open/close to kill JPEG speckle that would
   otherwise become dozens of unstitchable 1 mm islands.
5. **Contour tracing** — per colour layer, with hierarchy so holes stay holes.
6. **Smoothing & simplification** — the difference between a traced logo that
   looks hand-digitised and one that looks pixelated.

Potrace is used when the binary is available (better curve fitting on
high-contrast art); otherwise the OpenCV contour path handles everything.
"""

from __future__ import annotations

import io
import logging
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from .geometry import Ring, simplify_ring, smooth_ring
from .svg_parse import SvgShape
from .units import px_to_units

log = logging.getLogger(__name__)

MAX_WORKING_EDGE = 1400          # px; larger inputs are downscaled for tracing
MIN_CONTOUR_AREA_PX = 24         # drop specks smaller than this

# Decompression-bomb guard. A PNG of a solid colour compresses ~1000:1, so an
# 80 KB upload can decode to 84 megapixels and ~1 GB of RAM — comfortably past
# any sane upload size limit. Pillow's own default only warns at 89 Mpx and
# raises at 178 Mpx, both far above what this service should ever decode.
# 40 Mpx is roughly a 7000x5700 image: larger than any artwork worth tracing.
MAX_DECODED_PIXELS = 40_000_000

# k-means seeds its centres randomly. Two runs over the same artwork must
# produce the same palette, the same colour count and therefore the same
# compatibility score and quote — so the seed is pinned explicitly instead of
# relying on whatever the OpenCV build happens to default to.
KMEANS_SEED = 20240101
cv2.setRNGSeed(KMEANS_SEED)


@dataclass
class TraceOptions:
    max_colors: int = 6
    remove_background: bool = True
    background_tolerance: int = 28
    smooth: float = 0.35
    simplify_tolerance: float = 1.6      # in internal units after scaling
    min_area_px: int = MIN_CONTOUR_AREA_PX
    despeckle: int = 2                   # morphological kernel size, px
    target_width_mm: float | None = None
    use_potrace: bool = True


@dataclass
class TraceResult:
    shapes: list[SvgShape]
    palette: list[tuple[int, int, int]]
    width_px: int
    height_px: int
    scale_units_per_px: float
    background_removed: bool
    coverage: list[float]                # fraction of the design each colour covers

    def to_svg(self) -> str:
        width = self.width_px * self.scale_units_per_px
        height = self.height_px * self.scale_units_per_px
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.1f} {height:.1f}" '
            f'width="{width / 10:.2f}mm" height="{height / 10:.2f}mm">'
        ]
        for shape in self.shapes:
            if not shape.rings or shape.fill is None:
                continue
            d_parts = []
            for ring in shape.rings:
                if len(ring) < 3:
                    continue
                d_parts.append(
                    "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in ring) + " Z"
                )
            if not d_parts:
                continue
            r, g, b = shape.fill
            parts.append(
                f'<path d="{" ".join(d_parts)}" fill="#{r:02X}{g:02X}{b:02X}" '
                f'fill-rule="evenodd"/>'
            )
        parts.append("</svg>")
        return "".join(parts)


# ------------------------------------------------------------------- loading
class ImageTooLargeError(ValueError):
    """The image decodes to more pixels than we are willing to hold in memory."""


def load_image(data: bytes) -> np.ndarray:
    """Decode to an RGBA numpy array, EXIF-oriented.

    The dimension check happens on the header, *before* any pixel data is
    decoded, so a decompression bomb is rejected without allocating for it.
    """
    try:
        probe = Image.open(io.BytesIO(data))
        width, height = probe.size
    except Exception as exc:
        raise ValueError(f"Could not read the image file: {exc}") from exc

    pixels = width * height
    if pixels > MAX_DECODED_PIXELS:
        raise ImageTooLargeError(
            f"Image is {width}x{height} ({pixels / 1_000_000:.0f} megapixels), over the "
            f"{MAX_DECODED_PIXELS // 1_000_000} megapixel limit. Resize it before uploading — "
            "tracing downsamples to 1400 px anyway, so nothing is gained by a larger file."
        )

    try:
        image = Image.open(io.BytesIO(data))
    except Exception as exc:  # pragma: no cover - the probe above already opened it
        raise ValueError(f"Could not read the image file: {exc}") from exc
    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGBA", "RGB"):
        image = image.convert("RGBA")
    if image.mode == "RGB":
        image = image.convert("RGBA")
    return np.array(image)


def downscale(image: np.ndarray, max_edge: int = MAX_WORKING_EDGE) -> tuple[np.ndarray, float]:
    """Shrink for tracing; returns the image and the px-per-original-px factor."""
    h, w = image.shape[:2]
    longest = max(h, w)
    if longest <= max_edge:
        return image, 1.0
    factor = max_edge / longest
    resized = cv2.resize(
        image, (max(1, int(w * factor)), max(1, int(h * factor))), interpolation=cv2.INTER_AREA
    )
    return resized, factor


# ------------------------------------------------------- background handling
def build_alpha_mask(image: np.ndarray, tolerance: int, enabled: bool) -> tuple[np.ndarray, bool]:
    """Return a uint8 foreground mask (255 = keep) and whether we removed anything."""
    h, w = image.shape[:2]
    alpha = image[:, :, 3]
    if alpha.min() < 250:
        return ((alpha > 24).astype(np.uint8) * 255, True)

    if not enabled:
        return (np.full((h, w), 255, np.uint8), False)

    rgb = image[:, :, :3].astype(np.int16)
    corners = [rgb[0, 0], rgb[0, w - 1], rgb[h - 1, 0], rgb[h - 1, w - 1]]
    reference = np.median(np.array(corners), axis=0)

    distance = np.sqrt(((rgb - reference) ** 2).sum(axis=2))
    background = (distance <= tolerance).astype(np.uint8)

    # Only remove background that is actually connected to the border - an
    # interior area that happens to match the backdrop colour is part of the art.
    num, labels = cv2.connectedComponents(background, connectivity=8)
    border_labels = set(labels[0, :]) | set(labels[-1, :]) | set(labels[:, 0]) | set(labels[:, -1])
    border_labels.discard(0)
    if not border_labels:
        return (np.full((h, w), 255, np.uint8), False)

    mask = np.isin(labels, list(border_labels))
    foreground = (~mask).astype(np.uint8) * 255
    removed_fraction = 1.0 - (foreground > 0).mean()
    if removed_fraction < 0.02:
        return (np.full((h, w), 255, np.uint8), False)
    return (foreground, True)


# ------------------------------------------------------------ colour reduction
def quantize(
    image: np.ndarray, mask: np.ndarray, max_colors: int
) -> tuple[np.ndarray, list[tuple[int, int, int]]]:
    """k-means in L*a*b*; returns a label image (-1 = background) and a palette."""
    h, w = image.shape[:2]
    rgb = image[:, :, :3]
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)

    keep = mask > 0
    samples = lab[keep].astype(np.float32)
    if samples.size == 0:
        return (np.full((h, w), -1, np.int32), [])

    unique_colors = np.unique(rgb[keep].reshape(-1, 3), axis=0)
    k = int(max(1, min(max_colors, len(unique_colors))))

    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.6)
    cv2.setRNGSeed(KMEANS_SEED)   # reproducible clustering, see KMEANS_SEED
    _compactness, labels, centers = cv2.kmeans(
        samples, k, None, criteria, 4, cv2.KMEANS_PP_CENTERS
    )

    label_image = np.full((h, w), -1, np.int32)
    label_image[keep] = labels.flatten()

    centers_lab = centers.astype(np.uint8).reshape(-1, 1, 3)
    centers_rgb = cv2.cvtColor(centers_lab, cv2.COLOR_LAB2RGB).reshape(-1, 3)
    palette = [tuple(int(c) for c in color) for color in centers_rgb]
    return (label_image, palette)  # type: ignore[return-value]


def clean_layer(binary: np.ndarray, despeckle: int) -> np.ndarray:
    if despeckle <= 0:
        return binary
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (despeckle * 2 + 1, despeckle * 2 + 1))
    opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    return cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel)


# ------------------------------------------------------------------- tracing
def _contours_to_rings(
    binary: np.ndarray, scale: float, options: TraceOptions
) -> list[Ring]:
    contours, hierarchy = cv2.findContours(binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_TC89_KCOS)
    rings: list[Ring] = []
    if hierarchy is None:
        return rings
    for contour, meta in zip(contours, hierarchy[0]):
        if cv2.contourArea(contour) < options.min_area_px:
            continue
        points = [(float(p[0][0]) * scale, float(p[0][1]) * scale) for p in contour]
        if len(points) < 3:
            continue
        if options.smooth > 0:
            points = smooth_ring(points, options.smooth, 2)
        points = simplify_ring(points, options.simplify_tolerance)
        if len(points) >= 3:
            rings.append(points)
    return rings


def _potrace_available() -> bool:
    return shutil.which("potrace") is not None


def _trace_with_potrace(binary: np.ndarray, scale: float, options: TraceOptions) -> list[Ring] | None:
    """Run the potrace binary over one layer and parse the SVG it returns."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            pbm_path = Path(tmp) / "layer.pbm"
            svg_path = Path(tmp) / "layer.svg"
            # potrace traces black-on-white.
            Image.fromarray(255 - binary).convert("1").save(pbm_path)
            subprocess.run(
                [
                    "potrace", str(pbm_path), "-s", "-o", str(svg_path),
                    "--turdsize", str(max(2, options.min_area_px // 4)),
                    "--alphamax", "1.0", "--opttolerance", "0.2",
                ],
                check=True, capture_output=True, timeout=45,
            )
            from .svg_parse import parse_path_data

            svg_text = svg_path.read_text()
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        log.debug("potrace unavailable or failed (%s); using OpenCV contours", exc)
        return None

    import re

    rings: list[Ring] = []
    # potrace emits a single <path> with a transform; recover the scale/translate.
    transform = re.search(r'transform="translate\(([-\d.]+),([-\d.]+)\)\s*scale\(([-\d.]+),([-\d.]+)\)"', svg_text)
    tx, ty, sx, sy = (0.0, 0.0, 1.0, 1.0)
    if transform:
        tx, ty, sx, sy = (float(transform.group(i)) for i in range(1, 5))
    for match in re.finditer(r'\sd="([^"]+)"', svg_text):
        for points, _closed in parse_path_data(match.group(1), 0.6):
            mapped = [((x * sx + tx) * scale, (y * sy + ty) * scale) for x, y in points]
            mapped = simplify_ring(mapped, options.simplify_tolerance)
            if len(mapped) >= 3:
                rings.append(mapped)
    return rings or None


def trace_image(data: bytes, options: TraceOptions | None = None) -> TraceResult:
    """Full raster -> vector conversion."""
    options = options or TraceOptions()
    original = load_image(data)
    working, resize_factor = downscale(original)
    h, w = working.shape[:2]

    mask, removed = build_alpha_mask(working, options.background_tolerance, options.remove_background)
    labels, palette = quantize(working, mask, options.max_colors)
    if not palette:
        raise ValueError(
            "The image is empty after background removal. Try turning background "
            "removal off, or upload artwork with more contrast."
        )

    # Units per working pixel: honour a requested finished width, else assume
    # the source is 96 dpi artwork.
    if options.target_width_mm:
        scale = (options.target_width_mm * 10.0) / max(1, w)
    else:
        scale = px_to_units(1.0 / max(resize_factor, 1e-6)) * resize_factor

    use_potrace = options.use_potrace and _potrace_available()
    shapes: list[SvgShape] = []
    coverage: list[float] = []
    total_px = float(max(1, int((labels >= 0).sum())))

    # Paint darker colours last so outlines land on top of fills.
    def luminance(color: tuple[int, int, int]) -> float:
        return 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]

    order = sorted(range(len(palette)), key=lambda i: -luminance(palette[i]))

    for index in order:
        layer = ((labels == index).astype(np.uint8)) * 255
        pixel_count = int((layer > 0).sum())
        if pixel_count < options.min_area_px:
            continue
        layer = clean_layer(layer, options.despeckle)
        if (layer > 0).sum() < options.min_area_px:
            continue

        rings: list[Ring] | None = None
        if use_potrace:
            rings = _trace_with_potrace(layer, scale, options)
        if rings is None:
            rings = _contours_to_rings(layer, scale, options)
        if not rings:
            continue

        shapes.append(
            SvgShape(
                rings=rings,
                fill=palette[index],
                stroke=None,
                stroke_width=0.0,
                closed=True,
                label=f"layer_{index + 1}",
                kind="traced",
            )
        )
        coverage.append(round(pixel_count / total_px, 4))

    if not shapes:
        raise ValueError(
            "No stitchable shapes were found. The artwork may be too low "
            "contrast or too small — try a larger, cleaner source image."
        )

    return TraceResult(
        shapes=shapes,
        palette=[palette[i] for i in order if i < len(palette)],
        width_px=w,
        height_px=h,
        scale_units_per_px=scale,
        background_removed=removed,
        coverage=coverage,
    )


def edge_preview(data: bytes) -> bytes:
    """Canny edge map, shown in the studio so the user can see what the
    vectorizer is keying on before committing to a conversion."""
    image = load_image(data)
    working, _ = downscale(image, 900)
    gray = cv2.cvtColor(working[:, :, :3], cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    median = float(np.median(blurred))
    lower = int(max(0, 0.66 * median))
    upper = int(min(255, 1.33 * median))
    edges = cv2.Canny(blurred, lower, upper)
    buffer = io.BytesIO()
    Image.fromarray(255 - edges).save(buffer, format="PNG")
    return buffer.getvalue()


def image_complexity(data: bytes) -> dict:
    """Cheap structural metrics that feed the compatibility score."""
    image = load_image(data)
    working, _ = downscale(image, 900)
    rgb = working[:, :, :3]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 60, 160)
    edge_density = float((edges > 0).mean())

    unique = len(np.unique(rgb.reshape(-1, 3) // 8, axis=0))
    laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    contours, _ = cv2.findContours(
        cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
        cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
    )
    areas = [cv2.contourArea(c) for c in contours if cv2.contourArea(c) > 4]
    total_area = float(working.shape[0] * working.shape[1])
    small_parts = sum(1 for a in areas if a / total_area < 0.001)

    return {
        "width_px": int(image.shape[1]),
        "height_px": int(image.shape[0]),
        "edge_density": round(edge_density, 4),
        "distinct_colors": int(unique),
        "sharpness": round(laplacian_var, 2),
        "shape_count": len(areas),
        "small_part_count": int(small_parts),
        "has_alpha": bool(image.shape[2] == 4 and image[:, :, 3].min() < 250),
        "aspect_ratio": round(image.shape[1] / max(1, image.shape[0]), 3),
        "megapixels": round(image.shape[0] * image.shape[1] / 1_000_000, 2),
    }


def thumbnail(data: bytes, size: int = 512) -> bytes:
    image = Image.open(io.BytesIO(data))
    image = ImageOps.exif_transpose(image).convert("RGBA")
    image.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", image.size, (255, 255, 255, 0))
    canvas.alpha_composite(image)
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def dominant_colors(data: bytes, count: int = 6) -> list[dict]:
    """Palette extraction for the analyzer report."""
    image = load_image(data)
    working, _ = downscale(image, 600)
    mask, _ = build_alpha_mask(working, 28, True)
    labels, palette = quantize(working, mask, count)
    out: list[dict] = []
    total = float(max(1, int((labels >= 0).sum())))
    for index, color in enumerate(palette):
        share = float((labels == index).sum()) / total
        if share <= 0.001:
            continue
        out.append(
            {
                "hex": f"#{color[0]:02X}{color[1]:02X}{color[2]:02X}",
                "rgb": list(color),
                "share": round(share, 4),
            }
        )
    out.sort(key=lambda c: -c["share"])
    return out


def estimate_line_widths(data: bytes) -> dict:
    """Distance transform on the ink mask: tells us whether the artwork has
    hairlines that will not survive as thread."""
    image = load_image(data)
    working, _ = downscale(image, 900)
    gray = cv2.cvtColor(working[:, :, :3], cv2.COLOR_RGB2GRAY)
    _t, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    dist = cv2.distanceTransform(binary, cv2.DIST_L2, 5)
    values = dist[dist > 0]
    if values.size == 0:
        return {"min_px": 0.0, "median_px": 0.0, "p05_px": 0.0}
    return {
        "min_px": round(float(values.min()) * 2, 2),
        "median_px": round(float(np.median(values)) * 2, 2),
        "p05_px": round(float(np.percentile(values, 5)) * 2, 2),
    }
