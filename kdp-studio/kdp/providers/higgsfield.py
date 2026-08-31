"""The Higgsfield provider.

**Operational note, and the reason this file is mostly a boundary.**

Higgsfield is reachable in two quite different ways, and they are not
interchangeable:

1. *As an MCP server attached to Claude.* The agent calls the tool, gets an
   image back, and writes it into the book directory. The pipeline never sees a
   credential because it never makes the call. This is the intended workflow,
   and it keeps the blast radius of a key to the Claude session.

2. *As a direct HTTP API from this package.* The pipeline holds a key and calls
   the service itself. That is what the class below is for.

At the time of writing, no Higgsfield MCP server is attached to this session and
no public API contract was available to implement against, so the direct client
cannot be written honestly — a hand-guessed endpoint would look like an
integration and fail on first contact. Rather than ship that, this provider
reports itself unavailable with an explanation, and the pipeline runs through
the MCP route or the mock.

To complete the direct integration: fill in ``_endpoint`` and ``_request_body``
from Higgsfield's own API documentation and implement ``generate``. Everything
around it — the request/result types, provenance recording, attempt tracking,
retries — is already in place and provider-agnostic.
"""

from __future__ import annotations

import os

from .base import (
    GenerationRequest,
    GenerationResult,
    ProviderUnavailable,
    env_credential,
)

#: Environment variables searched for a key, in order. Never hard-code one.
CREDENTIAL_ENV_VARS = ("HIGGSFIELD_API_KEY", "KDP_HIGGSFIELD_API_KEY")

#: Set to "mcp" when images arrive through the Claude MCP integration, or
#: "http" once the direct client below is implemented.
MODE_ENV_VAR = "KDP_HIGGSFIELD_MODE"


class HiggsfieldProvider:
    """Direct HTTP client. Unavailable until the API contract is filled in."""

    name = "higgsfield"

    def __init__(self, model: str = "higgsfield-image") -> None:
        self.model = model

    @property
    def mode(self) -> str:
        return os.environ.get(MODE_ENV_VAR, "mcp")

    def configured(self) -> bool:
        return env_credential(*CREDENTIAL_ENV_VARS) is not None

    def available(self) -> bool:
        # Deliberately False in "mcp" mode: in that workflow the agent calls the
        # tool and this class must not pretend it can.
        return self.mode == "http" and self.configured()

    def readiness(self) -> dict[str, bool]:
        """Each precondition, separately, so a report can name what is missing."""
        return {
            "http_mode_selected": self.mode == "http",
            "credential_present": self.configured(),
            "http_client_implemented": False,
        }

    def unavailable_reason(self) -> str:
        if self.mode == "mcp":
            return (
                "Higgsfield is in MCP mode, which is not this class's job. In "
                "that mode an agent with the Higgsfield MCP tool attached calls "
                "it, writes the PNG into the book's assets directory, and "
                "registers it with `kdp register-asset <manifest> <page-id> "
                "<file> --provider higgsfield --model <model>` — which "
                "inspects the image, hashes it and records provenance. The "
                "credential stays in the session and never reaches a manifest. "
                f"Set {MODE_ENV_VAR}=http to use a direct client instead, once "
                "one exists."
            )
        if not self.configured():
            return (
                "Higgsfield HTTP mode is selected but no credential is present. "
                f"Set one of: {', '.join(CREDENTIAL_ENV_VARS)}. The key is read "
                "from the environment at call time and is never written to a "
                "manifest, a log or a provenance record."
            )
        return (
            "The Higgsfield HTTP client is not implemented. Its API contract "
            "was not available when this module was written, and a guessed "
            "endpoint would look like a working integration while failing on "
            "first contact. Implement _endpoint/_request_body/generate against "
            "Higgsfield's documentation."
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        raise ProviderUnavailable(self.unavailable_reason())
