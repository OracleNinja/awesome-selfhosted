"""Google Imagen, through the official ``google-genai`` SDK.

Implemented against the SDK's own typed surface rather than a remembered REST
shape — ``Client.models.generate_images(model, prompt, config)`` taking a
``GenerateImagesConfig`` and returning ``GenerateImagesResponse`` with
``generated_images[].image.image_bytes``. That contract is readable from the
installed package, which is the difference between this provider and the
Higgsfield one: there, no contract was available, so no client was written.

**Credentials are never picked up ambiently.** The key is read from the
environment at call time by name, and ``vertexai=False`` is passed explicitly so
that application-default credentials, a gcloud token, or a stray
``GOOGLE_GENAI_USE_VERTEXAI`` cannot silently route generation onto somebody's
cloud project. A provider that quietly spends whatever credentials happen to be
lying around is worse than one that does not work.

**The prompt plan already carries what this API wants.** ``AssetPrompt.prohibited``
is exactly a negative prompt, and it is passed as one rather than being appended
to the positive prompt where a model tends to draw the thing it was told to
avoid.
"""

from __future__ import annotations

import os

from .base import (
    GenerationRequest,
    GenerationResult,
    ProviderUnavailable,
    env_credential,
)

#: Searched in order. Never hard-code a key, and never read a cloud credential
#: that was put in the environment for something else.
CREDENTIAL_ENV_VARS = ("KDP_GOOGLE_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY")

#: Overridable so a newer model can be used without a code change.
MODEL_ENV_VAR = "KDP_GOOGLE_IMAGE_MODEL"
DEFAULT_MODEL = "imagen-4.0-generate-001"

#: Line art has no people in it, and generating them carries likeness and
#: rights questions a coloring book does not need.
PERSON_GENERATION = "DONT_ALLOW"


def sdk_available() -> bool:
    try:
        import google.genai  # noqa: F401
    except ImportError:
        return False
    return True


class GoogleImagenProvider:
    """Generates one image per request through Imagen."""

    name = "google-imagen"

    def __init__(self, model: str | None = None, client: object | None = None) -> None:
        self.model = model or os.environ.get(MODEL_ENV_VAR, DEFAULT_MODEL)
        #: Injected in tests. Left None in production so the credential is read
        #: at call time rather than held on the instance.
        self._client = client

    # -- availability -------------------------------------------------------

    def configured(self) -> bool:
        return env_credential(*CREDENTIAL_ENV_VARS) is not None

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
                "No API key. Set one of: "
                f"{', '.join(CREDENTIAL_ENV_VARS)}. The key is read from the "
                "environment at call time and never written to a manifest, a "
                "log or a provenance record. Cloud credentials already in the "
                "environment for other purposes are deliberately NOT used."
            )
        return "available"

    # -- generation ---------------------------------------------------------

    def _build_client(self):
        from google import genai

        key = env_credential(*CREDENTIAL_ENV_VARS)
        if key is None:
            raise ProviderUnavailable(self.unavailable_reason())
        # vertexai=False explicitly: without it the SDK may route to Vertex via
        # ambient project/ADC settings and bill a project nobody chose.
        return genai.Client(api_key=key, vertexai=False)

    def _config(self, request: GenerationRequest):
        from google.genai import types

        options = dict(request.options or {})
        config: dict = {
            "number_of_images": 1,
            "person_generation": PERSON_GENERATION,
            "output_mime_type": "image/png",
        }
        if request.prompt_negative:
            config["negative_prompt"] = request.prompt_negative
        if options.get("aspect_ratio"):
            config["aspect_ratio"] = options["aspect_ratio"]
        if options.get("image_size"):
            config["image_size"] = options["image_size"]
        # Deterministic where the caller asks for it. Imagen rejects a seed
        # alongside watermarking, so the two are not combined here.
        if options.get("seed") is not None:
            config["seed"] = options["seed"]
            config["add_watermark"] = False
        return types.GenerateImagesConfig(**config)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if not self.available():
            raise ProviderUnavailable(self.unavailable_reason())

        client = self._client or self._build_client()

        try:
            response = client.models.generate_images(
                model=self.model,
                # The positive half only. The prohibitions go in the negative
                # prompt; repeating them here is how a model ends up drawing
                # the thing it was told to leave out.
                prompt=request.prompt_positive,
                config=self._config(request),
            )
        except Exception as exc:  # noqa: BLE001
            # Every failure becomes a recorded attempt rather than an exception:
            # a page that could not be made must stay visibly missing.
            return GenerationResult(
                ok=False,
                page_id=request.page_id,
                provider=self.name,
                model=self.model,
                reason=f"{type(exc).__name__}: {exc}",
            )

        images = getattr(response, "generated_images", None) or []
        if not images:
            return GenerationResult(
                ok=False,
                page_id=request.page_id,
                provider=self.name,
                model=self.model,
                reason="the model returned no images",
            )

        first = images[0]
        filtered = getattr(first, "rai_filtered_reason", None)
        payload = getattr(first, "image", None)
        data = getattr(payload, "image_bytes", None) if payload else None

        if not data:
            return GenerationResult(
                ok=False,
                page_id=request.page_id,
                provider=self.name,
                model=self.model,
                reason=(
                    f"blocked by the responsible-AI filter: {filtered}"
                    if filtered
                    else "the response carried no image bytes"
                ),
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

        mime = getattr(payload, "mime_type", "") or "image/png"
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
                "enhanced_prompt": getattr(first, "enhanced_prompt", None),
                "rai_filtered_reason": filtered,
            },
        )
