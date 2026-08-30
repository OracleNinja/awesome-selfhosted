"""Exception types.

Kept small on purpose. Validation problems that a user should see are
*findings* on a gate result, not exceptions; exceptions are for programmer
error and malformed input that cannot be represented at all.
"""

from __future__ import annotations


class KDPError(Exception):
    """Base class for every error raised by this package."""


class SpecError(KDPError):
    """A print specification was requested that this revision does not define."""


class ModelError(KDPError):
    """A record could not be built from the supplied data."""


class PipelineError(KDPError):
    """An illegal stage transition was attempted."""
