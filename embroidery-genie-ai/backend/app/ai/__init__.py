"""AI cost control.

Every external model call in Embroidery Genie AI goes through this package.
The rule the rest of the codebase follows is simple: **if code can do it
reliably, code does it.** Scores, stitch counts, thread matching, machine
compatibility, pricing, validation and format conversion are all deterministic
and stay that way. A model is used for exactly one thing — describing what a
piece of artwork depicts — and that description can never move a number a
customer is quoted against.

Module map
----------
``routing``      capability tiers → configured model ids (the only place a
                 model name appears)
``operations``   the registry; an operation must declare its budget before it
                 can be called
``payload``      context minimisation and artwork hashing
``pricing``      tokens → dollars, from external configuration, never guessed
``ledger``       append-only meter, one row per attempt
``budget``       five spend scopes, threshold alerting, hard stop
``cache``        tenant-scoped result cache
``providers``    thin SDK adapters with auto-retry disabled
``gateway``      the choke point that wires all of the above together
"""

from app.ai.gateway import AIContext, AIOutcome, run_vision_analysis
from app.ai.routing import ModelTier, Provider

__all__ = [
    "AIContext",
    "AIOutcome",
    "ModelTier",
    "Provider",
    "run_vision_analysis",
]
