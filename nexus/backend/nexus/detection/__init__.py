"""Detection and risk scoring.

Layering, outermost to innermost:

``engine``
    The analysis pass. Owns persistence, deduplication, evidence linking,
    auditing and the watermark. The only module here that writes.
``rules``
    Individual deterministic detectors. Read-only, and each one explains itself.
``risk``
    The scoring model: a weighted sum of named factors, computed as a pure
    function so a score can be re-derived from stored inputs.
``config``
    Rule parameters and weights, stored in ``app_settings`` and validated on
    both read and write.
``observation``
    The canonical observation model — the typed, provenance-carrying projection
    of ``security_events`` that every detector consumes.
``base``
    The detector contract, and the severity/confidence conventions that keep
    those two axes separate.

Nothing in this package fabricates data. Every finding cites the observations it
was derived from, every score lists the factors that produced it, and a rule
that cannot run says so with a remedy rather than returning an empty result.
"""

from nexus.detection.engine import AnalysisResult, DetectionEngine
from nexus.detection.observation import Observation, Provenance, from_event
from nexus.detection.risk import RiskAssessment, RiskService, compute_device_risk

__all__ = [
    "AnalysisResult",
    "DetectionEngine",
    "Observation",
    "Provenance",
    "RiskAssessment",
    "RiskService",
    "compute_device_risk",
    "from_event",
]
