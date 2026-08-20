"""The detection rules that ship with NEXUS.

Every rule in this package is **deterministic and evidence-based**: it reaches a
conclusion by comparing stored observations against stated thresholds, and the
comparison is recorded in the finding it produces. There is no model here, no
learned baseline, and no scoring by a language model — a conclusion that cannot
be re-derived from the stored evidence cannot be defended, and on a security
console an indefensible conclusion is worse than none.

Order matters only for readability of the output; rules are independent and none
observes another's findings within a pass.
"""

from nexus.detection.base import Detector
from nexus.detection.rules.identity_change import IdentityChangeDetector
from nexus.detection.rules.new_device import NewDeviceDetector
from nexus.detection.rules.repeated_anomaly import RepeatedAnomalyDetector
from nexus.detection.rules.unexpected_communication import UnexpectedCommunicationDetector

# Instantiated once: detectors hold no per-run state, which is what allows the
# engine to reuse them across passes and across scopes.
ALL_DETECTORS: tuple[Detector, ...] = (
    NewDeviceDetector(),
    IdentityChangeDetector(),
    RepeatedAnomalyDetector(),
    UnexpectedCommunicationDetector(),
)

__all__ = [
    "ALL_DETECTORS",
    "IdentityChangeDetector",
    "NewDeviceDetector",
    "RepeatedAnomalyDetector",
    "UnexpectedCommunicationDetector",
]
