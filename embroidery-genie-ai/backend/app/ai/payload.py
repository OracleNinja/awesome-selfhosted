"""Context minimisation and artwork hashing.

Two jobs, both about not paying for bytes that carry no information:

**Normalisation.** A 4000 x 3000 upload costs roughly sixteen times what a
1000 x 750 one does, and the semantic pass — "what is this a picture of, does
it contain lettering" — reads exactly the same from either. So artwork is
downscaled, flattened onto white, stripped of EXIF and re-encoded before it
goes anywhere near a provider. Nothing else about the design is sent: not the
filename, not the customer, not the analysis record, not the org.

**Hashing.** The hash is taken over the *normalised* bytes rather than the raw
upload, which widens what counts as "the same artwork": a file re-saved at a
different PNG compression level, stripped of metadata, flattened from RGBA, or
exported by a different tool hashes alike as long as the pixels match. Two
renders of the same logo at *different resolutions* do not — resampling changes
pixels, and this is an exact hash.

That is a deliberate limit. A perceptual hash would catch the resolution case
and would also, sooner or later, collide two different logos and serve one
customer a description of another's artwork. An exact hash can only ever miss,
and a miss costs one API call.
"""

from __future__ import annotations

import hashlib
import io
import math
from dataclasses import dataclass

from PIL import Image

from app.core.config import settings

#: Anthropic documents image token cost as approximately (width x height) / 750.
#: Used for pre-flight budgeting only; the ledger records the provider's own
#: reported usage once the call returns.
PIXELS_PER_TOKEN = 750

#: Roughly four characters per token. A deliberate over-estimate for budgeting.
CHARS_PER_TOKEN = 3.5


@dataclass(frozen=True)
class NormalizedArtwork:
    data: bytes
    media_type: str
    width: int
    height: int
    #: SHA-256 over ``data`` — the canonical artwork identity.
    artwork_hash: str
    #: SHA-256 over the bytes as uploaded, for duplicate-upload reporting.
    source_hash: str
    original_width: int
    original_height: int
    original_bytes: int

    @property
    def estimated_image_tokens(self) -> int:
        return math.ceil((self.width * self.height) / PIXELS_PER_TOKEN)

    def to_meta(self) -> dict:
        return {
            "artwork_hash": self.artwork_hash,
            "source_hash": self.source_hash,
            "sent_px": f"{self.width}x{self.height}",
            "original_px": f"{self.original_width}x{self.original_height}",
            "original_bytes": self.original_bytes,
            "sent_bytes": len(self.data),
        }


class ArtworkUnreadable(ValueError):
    """The bytes are not an image we can normalise."""


def estimate_text_tokens(text: str) -> int:
    return math.ceil(len(text or "") / CHARS_PER_TOKEN)


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_artwork(
    image_bytes: bytes,
    content_type: str = "image/png",
    max_edge: int | None = None,
) -> NormalizedArtwork:
    """Reduce artwork to the smallest form that still answers the question.

    Raises ``ArtworkUnreadable`` rather than sending something a provider will
    reject — a rejected request still costs a round trip and still has to be
    retried, and we would rather fail here for free.
    """
    max_edge = max_edge or settings.ai_image_max_edge_px
    source_hash = content_hash(image_bytes)

    try:
        with Image.open(io.BytesIO(image_bytes)) as opened:
            original_width, original_height = opened.size
            image = opened.convert("RGBA")
    except Exception as exc:  # noqa: BLE001 - Pillow raises a wide family here
        raise ArtworkUnreadable(f"Artwork could not be decoded: {exc}") from exc

    if original_width <= 0 or original_height <= 0:
        raise ArtworkUnreadable("Artwork has no pixels.")

    # Flatten transparency onto white. A model shown a checkerboard or a black
    # background describes the background; embroidery artwork is almost always
    # meant to be read against a light garment.
    flattened = Image.new("RGB", image.size, (255, 255, 255))
    flattened.paste(image, mask=image.split()[3])
    image.close()

    longest = max(flattened.size)
    if longest > max_edge:
        scale = max_edge / longest
        flattened = flattened.resize(
            (max(1, round(flattened.width * scale)), max(1, round(flattened.height * scale))),
            Image.LANCZOS,
        )

    buffer = io.BytesIO()
    # Re-encoding through a fresh buffer drops EXIF, colour profiles, XMP and
    # any other metadata the upload carried. None of it is useful to the model
    # and some of it (GPS, author, original filename) is customer data.
    flattened.save(buffer, format="JPEG", quality=settings.ai_image_jpeg_quality, optimize=True)
    data = buffer.getvalue()
    width, height = flattened.size
    flattened.close()

    return NormalizedArtwork(
        data=data,
        media_type="image/jpeg",
        width=width,
        height=height,
        artwork_hash=content_hash(data),
        source_hash=source_hash,
        original_width=original_width,
        original_height=original_height,
        original_bytes=len(image_bytes),
    )


def shrink_to_token_budget(
    artwork: NormalizedArtwork,
    prompt_tokens: int,
    max_input_tokens: int,
) -> NormalizedArtwork | None:
    """Re-normalise smaller until the estimated input fits, or give up.

    Returns ``None`` when even a minimal render cannot fit, which the gateway
    turns into a clean rejection. Deterministic reduction first, refusal second,
    unbounded spend never.
    """
    if prompt_tokens + artwork.estimated_image_tokens <= max_input_tokens:
        return artwork

    for edge in (768, 512, 384, 256):
        if edge >= max(artwork.width, artwork.height):
            continue
        candidate = normalize_artwork(artwork.data, artwork.media_type, max_edge=edge)
        if prompt_tokens + candidate.estimated_image_tokens <= max_input_tokens:
            return candidate
    return None
