"""Line art from vector source, rasterised locally. No API, no key, no cost.

The other providers ask a hosted model for a picture. This one asks nothing:
the artwork for a page is an SVG that lives beside the book, and generating the
page means rasterising that SVG at the exact pixel size the request asks for.

**Why vector, for this particular job.** A coloring page is not a photograph.
It is closed black outlines of uniform weight on white — which is what SVG *is*,
and what a raster generator has to be coaxed into approximating. Three of the
requirements that make hosted generation hard fall out of the format for free:

* *Resolution.* Vector art has none until it is rasterised, so 300 DPI over the
  live area is a rendering parameter rather than a thing to hope the model
  honours. This is what the 768x768 disappointment cost a paid call to learn.
* *Purity.* A white ``<rect>`` under black strokes gives a genuinely white
  background and genuinely black ink, so the shading, midtone and
  background checks pass by construction instead of by luck.
* *Closed regions.* A closed ``<path>`` is closed. Fills cannot leak through a
  hairline that is not there.

**What it does not give you** is a model's imagination. The SVG has to be
written by something, and here that something is the ``kdp-asset-generator``
agent working from the page's own prompt. That makes the artwork AI-authored,
so ``ai_role`` stays ``GENERATED`` and the AI disclosure applies exactly as it
would for a hosted model. Rasterising is mechanical and changes nothing about
that.

**Renderer.** Chromium, already installed in this environment for Playwright.
It is used headless with a fixed window and a device scale factor of 1, so the
output is exactly the requested size or the attempt fails — a page that came
back smaller than asked for is the failure this whole provider exists to avoid,
and it is never quietly resampled up to hide it.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .base import GenerationRequest, GenerationResult, ProviderUnavailable

#: Where a book keeps its per-page vector source. One SVG per page id.
#: Set per book, because a provider instance has no idea which book is running:
#: ``KDP_VECTOR_ART_DIR=books/bold-easy-garden/art``.
ART_DIR_ENV = "KDP_VECTOR_ART_DIR"

#: An explicit override for the renderer, if the discovered one is wrong.
CHROMIUM_ENV = "KDP_CHROMIUM"

#: Where Playwright puts its browsers in this image, plus the usual names on a
#: PATH. Searched in order; the first that exists wins.
CHROMIUM_CANDIDATES = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
)

#: Chromium exits non-zero or hangs on a malformed document; a page of line art
#: renders in a few seconds, so a minute is already generous.
RENDER_TIMEOUT_SECONDS = 60


def find_chromium() -> str | None:
    """The renderer's path, or None. Explicit override first, then discovery."""
    override = os.environ.get(CHROMIUM_ENV)
    if override:
        return override if Path(override).is_file() else None

    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if root:
        # chromium-1194/chrome-linux/chrome, and the headless shell beside it.
        for pattern in ("chromium-*/chrome-linux/chrome", "chromium-*/chrome-mac/*"):
            for found in sorted(Path(root).glob(pattern)):
                if found.is_file() and os.access(found, os.X_OK):
                    return str(found)

    for name in CHROMIUM_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


def art_dir() -> Path | None:
    value = os.environ.get(ART_DIR_ENV)
    return Path(value) if value else None


def source_for(page_id: str, directory: Path) -> Path:
    return directory / f"{page_id}.svg"


def wrap(svg: str) -> str:
    """Put the drawing in a page that fills the viewport exactly.

    The SVG's own width and height attributes are deliberately overridden. What
    decides the output size is the window, so a source file that happens to
    declare inches, or nothing at all, still renders at the requested pixels.
    The viewBox keeps the proportions; a mismatch letterboxes in white rather
    than stretching the drawing, because distorted line art is worse than a
    margin.
    """
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0;background:#fff;overflow:hidden}"
        "svg{display:block;width:100vw;height:100vh}</style>"
        f"{svg}"
    )


class LocalVectorProvider:
    """Rasterises a page's SVG source at the requested size."""

    name = "local-vector"

    def __init__(self, directory: Path | str | None = None, chromium: str | None = None) -> None:
        self._directory = Path(directory) if directory else None
        self._chromium = chromium
        self.model = "claude-svg/chromium"

    # -- availability -------------------------------------------------------

    @property
    def directory(self) -> Path | None:
        return self._directory or art_dir()

    @property
    def chromium(self) -> str | None:
        return self._chromium or find_chromium()

    def readiness(self) -> dict[str, bool]:
        directory = self.directory
        return {
            "renderer_present": self.chromium is not None,
            "art_dir_configured": directory is not None,
            "art_dir_exists": bool(directory and directory.is_dir()),
        }

    def available(self) -> bool:
        return all(self.readiness().values())

    def unavailable_reason(self) -> str:
        checks = self.readiness()
        if not checks["renderer_present"]:
            return (
                "No Chromium to rasterise with. It is normally already present "
                f"for Playwright; set {CHROMIUM_ENV} to its path if it is "
                "installed somewhere unusual."
            )
        if not checks["art_dir_configured"]:
            return (
                f"Set {ART_DIR_ENV} to the book's vector source directory, e.g. "
                f"{ART_DIR_ENV}=books/<book-id>/art. One SVG per page id. A "
                "provider instance is not told which book is running, so this "
                "is how it finds the artwork."
            )
        return (
            f"{self.directory} does not exist. Create it and put one SVG per "
            "page in it, named <PAGE-ID>.svg."
        )

    # -- generation ---------------------------------------------------------

    def _failed(self, request, reason, *, retryable=True, **meta) -> GenerationResult:
        return GenerationResult(
            ok=False,
            page_id=request.page_id,
            provider=self.name,
            model=self.model,
            reason=reason,
            retryable=retryable,
            meta={k: v for k, v in meta.items() if v},
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if not self.available():
            raise ProviderUnavailable(self.unavailable_reason())

        directory = self.directory
        source = source_for(request.page_id, directory)
        if not source.is_file():
            # Settled: the file will not appear because it was asked for again.
            return self._failed(
                request,
                f"no vector source at {source}. This provider draws nothing by "
                "itself — the kdp-asset-generator agent writes one SVG per page "
                "from that page's prompt, and this renders it.",
                retryable=False,
            )

        try:
            svg = source.read_text(encoding="utf-8")
        except OSError as exc:
            return self._failed(request, f"cannot read {source}: {exc}", retryable=False)

        if "<svg" not in svg:
            return self._failed(
                request, f"{source} is not an SVG document", retryable=False
            )

        try:
            data = self._render(svg, request.width, request.height)
        except subprocess.TimeoutExpired:
            return self._failed(
                request,
                f"the renderer did not finish within {RENDER_TIMEOUT_SECONDS}s",
            )
        except Exception as exc:  # noqa: BLE001
            return self._failed(request, f"{type(exc).__name__}: {exc}")

        if not data:
            return self._failed(request, "the renderer produced no output")

        # Measured, never assumed. The whole point of this provider is that the
        # page comes back at the size the print geometry asked for, so a
        # mismatch is a failure rather than something to resample away.
        width = height = None
        try:
            from ..inspection.image import load_image

            decoded = load_image(data)
            width, height = decoded.width, decoded.height
        except Exception:  # noqa: BLE001
            pass

        if (width, height) != (request.width, request.height):
            return self._failed(
                request,
                f"rendered {width}x{height} px, not the requested "
                f"{request.width}x{request.height}. Refusing to resample: an "
                "upscaled page passes the pixel count and fails the eye.",
                retryable=False,
            )

        return GenerationResult(
            ok=True,
            page_id=request.page_id,
            data=data,
            provider=self.name,
            model=self.model,
            width=width,
            height=height,
            file_format="png",
            meta={
                "mime_type": "image/png",
                "source": str(source),
                # The drawing's identity, so a rasterised page can be traced
                # back to the exact vector it came from.
                "source_sha256": hashlib.sha256(svg.encode("utf-8")).hexdigest(),
                "renderer": Path(self.chromium).name,
                "cost": "none",
            },
        )

    def _render(self, svg: str, width: int, height: int) -> bytes:
        """One headless screenshot, in a scratch profile that is thrown away."""
        with tempfile.TemporaryDirectory() as work:
            work_path = Path(work)
            page = work_path / "page.html"
            page.write_text(wrap(svg), encoding="utf-8")
            out = work_path / "page.png"

            subprocess.run(
                [
                    self.chromium,
                    "--headless",
                    # Containers: no user namespace, and a small /dev/shm.
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    # Scale 1 with an exact window is what makes the output
                    # exactly the requested size rather than nearly it.
                    "--force-device-scale-factor=1",
                    f"--window-size={width},{height}",
                    "--default-background-color=FFFFFFFF",
                    f"--user-data-dir={work_path / 'profile'}",
                    f"--screenshot={out}",
                    page.as_uri(),
                ],
                capture_output=True,
                timeout=RENDER_TIMEOUT_SECONDS,
                check=False,
            )
            return out.read_bytes() if out.is_file() else b""
