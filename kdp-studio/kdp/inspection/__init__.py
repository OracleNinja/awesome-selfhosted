"""Real inspection of images and PDFs.

Two rules hold throughout this package:

**Capability is per-artefact, not per-install.** "Can I inspect this?" is a
better question than "is Pillow installed?". A PNG can be read with the
standard library alone; a JPEG cannot. So the loaders answer for the file in
front of them, and a gate blocks only when the specific artefact really cannot
be read.

**An inspector reports; it never repairs.** Everything here returns findings.
The read-only separation that makes the QA auditor trustworthy would be worth
nothing if the inspection layer beneath it could quietly fix what it found.
"""

from __future__ import annotations

from .image import (
    ImageInspection,
    ImageLoadError,
    inspect_image,
    load_image,
    perceptual_hash,
)
from .pdf import PDFInspection, PDFLoadError, inspect_pdf, pdf_reader_available

__all__ = [
    "ImageInspection",
    "ImageLoadError",
    "PDFInspection",
    "PDFLoadError",
    "inspect_image",
    "inspect_pdf",
    "load_image",
    "pdf_reader_available",
    "perceptual_hash",
]
