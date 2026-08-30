"""KDP Studio — a gated, auditable pipeline for original low-content books.

The package is deliberately dependency-free at its core. Everything that
decides whether a book may advance a stage is plain-stdlib Python so that a
gate can always be evaluated, on any machine, with a reproducible result.

Heavier work (rasterisation, PDF preflight, perceptual hashing) lives behind
interfaces that report ``blocked`` when their dependencies are absent. A
blocked gate is *not* a passing gate: missing tooling can never wave a book
through. See ``kdp.gates.base`` for why that distinction is load-bearing.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
