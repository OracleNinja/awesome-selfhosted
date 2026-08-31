"""Google Gemini image generation, through ``models.generate_content``.

This provider exists because the other Google one cannot reach print
resolution, and on the pinned SDK cannot run at all.

**Why not Imagen.** ``google-genai`` 2.20.0 deprecated ``models.generate_images``
and disabled it outside Gemini Enterprise Agent Platform mode, so an API key
reaches a ``ValueError`` rather than the network. And even where it does run, its
``GenerateImagesConfig.image_size`` offers only ``1K`` and ``2K``: 2048 px on the
long edge, against the 3150 px an 8.5x11 interior needs at 300 DPI. No
configuration of that path clears G9 for a full-page interior.

**What this uses instead.** ``models.generate_content`` with a Gemini image model
and ``ImageConfig(image_size="4K")`` — the only size in the installed SDK's typed
surface large enough for the page. Everything here is read off that surface:
``GenerateContentConfig.image_config``, ``ImageConfig.image_size`` documented as
``1K``, ``2K``, ``4K``, and a response of
``candidates[].content.parts[].inline_data.data``.

**Two capabilities Imagen had that this path does not.** Neither is worked
around silently:

* There is no negative-prompt field. ``_ImageConfig_to_mldev`` has none and
  ``GenerateContentConfig`` has none, so the prohibitions are composed into the
  text under their own heading rather than dropped. The separation the prompt
  plan makes is preserved in the message; it is just not a separate field.
* ``person_generation`` is rejected outright on the Gemini Developer API — the
  SDK raises rather than ignoring it. So the "no people" rule moves into the
  prohibitions, where it is at least stated, instead of being sent to an API
  that will refuse the whole request for mentioning it.

**Credentials.** One variable, ``KDP_GOOGLE_API_KEY``, read at call time. Not
``GOOGLE_API_KEY``, not ``GEMINI_API_KEY``, and no application-default
credential: a key that arrived in the environment for something else is not a
book budget. ``vertexai=False`` is passed explicitly and then *verified* on the
constructed client, because the failure mode being guarded against is a silent
one — a request billed to a cloud project nobody chose.
"""

from __future__ import annotations

import os

from .base import (
    GenerationRequest,
    GenerationResult,
    ProviderUnavailable,
    env_credential,
)

#: Exactly one variable, and deliberately not one the SDK itself would pick up.
#: ``google.genai`` falls back to GOOGLE_API_KEY and GEMINI_API_KEY when no key
#: is passed; naming this one differently means a stray key in the environment
#: cannot become this pipeline's spend by accident.
CREDENTIAL_ENV_VAR = "KDP_GOOGLE_API_KEY"

#: Overridable so a newer image model can be used without a code change. The
#: default is the model documented to offer 4K output; if it is retired, this is
#: the one line that changes.
MODEL_ENV_VAR = "KDP_GEMINI_IMAGE_MODEL"
DEFAULT_MODEL = "gemini-3-pro-image-preview"

#: An image, not a description of one.
RESPONSE_MODALITIES = ("IMAGE",)

#: The largest size ``ImageConfig`` documents. Anything smaller cannot clear
#: 300 DPI over a full-page 8.5x11 live area, so this is not a preference.
DEFAULT_IMAGE_SIZE = "4K"

#: How the prohibitions are introduced when they are folded into the text.
NEGATIVE_HEADING = "Do not include:"

#: Carried in the prohibitions because the API will not take it as a parameter.
#: Line art has no people in it, and generating them raises likeness questions a
#: colouring book does not need.
NO_PEOPLE_PROHIBITION = "people, faces, hands or any human figure"

#: Finish reasons that mean the model stopped rather than drew. Taken from the
#: SDK's ``FinishReason`` enum, which distinguishes image cases from text ones.
REFUSAL_FINISH_REASONS = frozenset({
    "SAFETY",
    "PROHIBITED_CONTENT",
    "BLOCKLIST",
    "RECITATION",
    "SPII",
    "IMAGE_SAFETY",
    "IMAGE_PROHIBITED_CONTENT",
    "IMAGE_RECITATION",
    "IMAGE_OTHER",
    "NO_IMAGE",
})


def sdk_available() -> bool:
    try:
        import google.genai  # noqa: F401
    except ImportError:
        return False
    return True


def _enum_name(value: object) -> str:
    """The string form of an SDK enum, a plain string, or ''."""
    if value is None:
        return ""
    return str(getattr(value, "value", value) or "")


def compose_prompt(request: GenerationRequest) -> str:
    """One message that keeps the positive and negative halves apart.

    ``generate_content`` has no negative-prompt field, so the choice is between
    losing the prohibitions and stating them. They are stated, under their own
    heading and after the subject, rather than mixed into the description of
    what to draw — listing what to avoid alongside what to draw is a well-known
    way to get a model to draw exactly the thing named.
    """
    positive = request.prompt_positive or request.prompt
    prohibitions = [p for p in (request.prompt_negative,) if p]
    if NO_PEOPLE_PROHIBITION not in request.prompt_negative:
        prohibitions.append(NO_PEOPLE_PROHIBITION)
    return f"{positive}\n\n{NEGATIVE_HEADING} {', '.join(prohibitions)}"


class GoogleGeminiImageProvider:
    """Generates one image per request through a Gemini image model."""

    name = "google-gemini"

    def __init__(self, model: str | None = None, client: object | None = None) -> None:
        self.model = model or os.environ.get(MODEL_ENV_VAR, DEFAULT_MODEL)
        #: Injected in tests. Left None in production so the credential is read
        #: at call time rather than held on the instance.
        self._client = client

    # -- availability -------------------------------------------------------

    def configured(self) -> bool:
        return env_credential(CREDENTIAL_ENV_VAR) is not None

    def readiness(self) -> dict[str, bool]:
        return {
            "sdk_installed": sdk_available(),
            "credential_present": self.configured(),
        }

    def available(self) -> bool:
        return self._client is not None or all(self.readiness().values())

    def unavailable_reason(self) -> str:
        checks = self.readiness()
        if not checks["sdk_installed"]:
            return (
                "The google-genai SDK is not installed. Install the generation "
                "extras (pip install -r requirements.txt)."
            )
        if not checks["credential_present"]:
            return (
                f"No API key. Set {CREDENTIAL_ENV_VAR}. It is read from the "
                "environment at call time and never written to a manifest, a "
                "log or a provenance record. GOOGLE_API_KEY, GEMINI_API_KEY and "
                "application-default credentials are deliberately NOT read: a "
                "credential that arrived for something else is not a budget."
            )
        return "available"

    # -- the request --------------------------------------------------------

    def _build_client(self):
        from google import genai

        key = env_credential(CREDENTIAL_ENV_VAR)
        if key is None:
            raise ProviderUnavailable(self.unavailable_reason())

        # vertexai=False explicitly: without it the SDK reads
        # GOOGLE_GENAI_USE_VERTEXAI / GOOGLE_GENAI_USE_ENTERPRISE and may route
        # to a cloud project via ADC. api_key explicitly: without it the SDK
        # falls back to GOOGLE_API_KEY or GEMINI_API_KEY from the environment.
        client = genai.Client(api_key=key, vertexai=False)

        # Verified rather than assumed. Being wrong here does not raise, it
        # bills someone, so the one thing worth an assertion is that the client
        # really is in Developer API mode with the key we chose.
        api = getattr(client, "_api_client", None)
        if api is not None:
            if getattr(api, "vertexai", False):
                raise ProviderUnavailable(
                    "the client came up in Gemini Enterprise Agent Platform "
                    "mode despite vertexai=False; refusing to generate rather "
                    "than bill a cloud project that was not chosen"
                )
            if getattr(api, "api_key", None) != key:
                raise ProviderUnavailable(
                    "the client is not using the key from "
                    f"{CREDENTIAL_ENV_VAR}; refusing to generate rather than "
                    "spend an ambient credential"
                )
        return client

    def _config(self, request: GenerationRequest):
        """The typed config, built from what the installed SDK actually takes.

        ``person_generation``, ``output_mime_type`` and
        ``output_compression_quality`` are all absent on purpose: on the Gemini
        Developer API the SDK raises on each of them rather than ignoring them,
        so sending one would fail the whole request.
        """
        from google.genai import types

        options = dict(request.options or {})
        image: dict = {"image_size": options.get("image_size") or DEFAULT_IMAGE_SIZE}
        if options.get("aspect_ratio"):
            image["aspect_ratio"] = options["aspect_ratio"]

        config: dict = {
            "response_modalities": list(RESPONSE_MODALITIES),
            "image_config": types.ImageConfig(**image),
        }
        if options.get("seed") is not None:
            config["seed"] = options["seed"]
        return types.GenerateContentConfig(**config)

    # -- generation ---------------------------------------------------------

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if not self.available():
            raise ProviderUnavailable(self.unavailable_reason())

        client = self._client or self._build_client()

        try:
            response = client.models.generate_content(
                model=self.model,
                contents=compose_prompt(request),
                config=self._config(request),
            )
        except Exception as exc:  # noqa: BLE001
            # Every failure becomes a recorded attempt rather than an exception:
            # a page that could not be made must stay visibly missing.
            return self._failed(request, f"{type(exc).__name__}: {exc}")

        return self._read(request, response)

    def _failed(self, request: GenerationRequest, reason: str, **meta) -> GenerationResult:
        return GenerationResult(
            ok=False,
            page_id=request.page_id,
            provider=self.name,
            model=self.model,
            reason=reason,
            meta={k: v for k, v in meta.items() if v},
        )

    def _read(self, request: GenerationRequest, response) -> GenerationResult:
        """Turn one response into a result, refusing anything short of an image."""
        provenance = _response_meta(response)

        feedback = getattr(response, "prompt_feedback", None)
        blocked = _enum_name(getattr(feedback, "block_reason", None))
        if blocked and blocked != "BLOCKED_REASON_UNSPECIFIED":
            return self._failed(
                request,
                f"the prompt was blocked before generation: {blocked}",
                **provenance,
            )

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return self._failed(
                request, "the model returned no candidates", **provenance
            )

        first = candidates[0]
        finish = _enum_name(getattr(first, "finish_reason", None))
        message = getattr(first, "finish_message", None) or ""

        blob = _first_image_blob(first)
        if blob is None:
            if finish in REFUSAL_FINISH_REASONS:
                detail = f": {message}" if message else ""
                return self._failed(
                    request,
                    f"the model refused to draw this page ({finish}){detail}",
                    finish_reason=finish,
                    **provenance,
                )
            return self._failed(
                request,
                "the response carried no image bytes"
                + (f" (finish reason {finish})" if finish else ""),
                finish_reason=finish,
                **provenance,
            )

        data = getattr(blob, "data", None)
        mime = getattr(blob, "mime_type", "") or ""
        if not data:
            return self._failed(
                request,
                f"the response carried an empty {mime or 'image'} part",
                finish_reason=finish,
                **provenance,
            )
        if not mime.startswith("image/"):
            # Fail closed rather than write bytes of an unknown kind into the
            # asset tree, where the only thing that would notice is a gate.
            return self._failed(
                request,
                f"the response part is {mime!r}, which is not an image",
                finish_reason=finish,
                **provenance,
            )

        # The model decides the output size, so the *actual* dimensions are
        # measured rather than assumed from the request. Asset QA compares the
        # provenance record against the file, and a guess here would fail it.
        width = height = None
        try:
            from ..inspection.image import load_image

            decoded = load_image(data)
            width, height = decoded.width, decoded.height
        except Exception:  # noqa: BLE001
            pass

        return GenerationResult(
            ok=True,
            page_id=request.page_id,
            data=data,
            provider=self.name,
            model=self.model,
            width=width,
            height=height,
            file_format=mime.rsplit("/", 1)[-1],
            meta={
                # Provenance, not secrets. No key, no project, no endpoint.
                "mime_type": mime,
                "finish_reason": finish,
                **provenance,
            },
        )


def _first_image_blob(candidate):
    """The first inline image part, ignoring any text the model also returned."""
    content = getattr(candidate, "content", None)
    parts = getattr(content, "parts", None) or []
    for part in parts:
        blob = getattr(part, "inline_data", None)
        if blob is not None and getattr(blob, "data", None) is not None:
            return blob
    # A part with a mime type but no bytes still has to be reported, so fall
    # back to the first inline part of any kind rather than pretending there
    # was none at all.
    for part in parts:
        blob = getattr(part, "inline_data", None)
        if blob is not None:
            return blob
    return None


def _response_meta(response) -> dict:
    """Identifiers worth keeping for a support ticket or a bill. No secrets."""
    usage = getattr(response, "usage_metadata", None)
    meta = {
        "model_version": getattr(response, "model_version", None),
        "response_id": getattr(response, "response_id", None),
    }
    for field in ("prompt_token_count", "candidates_token_count", "total_token_count"):
        value = getattr(usage, field, None)
        if value is not None:
            meta[field] = value
    return {k: v for k, v in meta.items() if v is not None}
