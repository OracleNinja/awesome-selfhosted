"""The Google Imagen provider, exercised without spending anything.

Every test here drives a stub shaped like the real SDK response — the same
attribute names the installed ``google.genai`` types actually carry
(``generated_images[].image.image_bytes``, ``.mime_type``,
``rai_filtered_reason``). Nothing reaches the network and no key is needed, so
CI never pays for a picture.
"""

from __future__ import annotations

import pytest

from kdp.providers import METERED, get_provider
from kdp.providers.base import GenerationRequest, ProviderUnavailable
from kdp.providers.google_imagen import (
    CREDENTIAL_ENV_VARS,
    PERSON_GENERATION,
    GoogleImagenProvider,
    sdk_available,
)
from kdp.providers.mock import _png
from conftest import (
    StubClient,
    StubGenerated,
    StubImage,
    StubResponse,
    stub_client_returning,
)


#: The provider builds a genuine ``GenerateImagesConfig``, so a stub client is
#: not enough to drive it — the SDK has to be installed. The stdlib-only CI job
#: has none, and a test that quietly degraded to a failed call there would
#: assert nothing while still reporting green.
requires_sdk = pytest.mark.skipif(
    not sdk_available(), reason="the generation extra is optional"
)


def _request(**overrides) -> GenerationRequest:
    fields = dict(
        page_id="PAGE-004",
        prompt_id="PROMPT-PAGE-004",
        prompt_version="v1",
        prompt="Subject: a fern\nMust not contain: colour",
        prompt_positive="Subject: a fern",
        prompt_negative="colour, shading, text",
        width=2520,
        height=3360,
        options={"aspect_ratio": "3:4"},
    )
    fields.update(overrides)
    return GenerationRequest(**fields)


def _ok_client(data: bytes | None = None) -> StubClient:
    return stub_client_returning(data or _png(64, 80, b"x"))


# --- availability -----------------------------------------------------------


def test_it_is_metered_so_generate_demands_confirm_spend():
    assert "google-imagen" in METERED


@requires_sdk
def test_unavailable_without_a_key(monkeypatch):
    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    provider = get_provider("google-imagen")
    assert provider.available() is False
    assert "No API key" in provider.unavailable_reason()


def test_readiness_names_each_precondition_separately(monkeypatch):
    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    checks = get_provider("google-imagen").readiness()
    assert checks["sdk_installed"] is sdk_available()
    assert checks["credential_present"] is False


def test_a_key_in_the_environment_makes_it_available(monkeypatch):
    monkeypatch.setenv("KDP_GOOGLE_API_KEY", "test-key-not-real")
    assert get_provider("google-imagen").available() is sdk_available()


@requires_sdk
def test_generating_without_a_key_raises_rather_than_calling(monkeypatch):
    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ProviderUnavailable, match="No API key"):
        get_provider("google-imagen").generate(_request())


def test_ambient_cloud_credentials_are_not_treated_as_a_key(monkeypatch):
    """The environment often carries cloud credentials for something else.

    Routing image generation onto whatever happens to be lying around would
    bill a project nobody chose.
    """
    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLOUDSDK_AUTH_ACCESS_TOKEN", "ya29.not-a-real-token")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-a-real-secret")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "someone-elses-project")
    assert get_provider("google-imagen").available() is False


# --- request shaping --------------------------------------------------------


@requires_sdk
def test_prohibitions_go_in_the_negative_prompt_not_the_positive_one():
    """Listing what to avoid positively is how a model draws exactly that."""
    client = _ok_client()
    GoogleImagenProvider(client=client).generate(_request())
    call = client.models.calls[0]
    assert call["prompt"] == "Subject: a fern"
    assert "Must not contain" not in call["prompt"]
    assert call["config"].negative_prompt == "colour, shading, text"


@requires_sdk
def test_people_are_never_generated():
    client = _ok_client()
    GoogleImagenProvider(client=client).generate(_request())
    config = client.models.calls[0]["config"]
    assert config.person_generation == PERSON_GENERATION == "DONT_ALLOW"


@requires_sdk
def test_one_image_per_request_and_png_output():
    client = _ok_client()
    GoogleImagenProvider(client=client).generate(_request())
    config = client.models.calls[0]["config"]
    assert config.number_of_images == 1
    assert config.output_mime_type == "image/png"


@requires_sdk
def test_aspect_ratio_is_passed_through():
    client = _ok_client()
    GoogleImagenProvider(client=client).generate(_request())
    assert client.models.calls[0]["config"].aspect_ratio == "3:4"


@requires_sdk
def test_a_seed_turns_off_watermarking_because_imagen_rejects_both():
    client = _ok_client()
    GoogleImagenProvider(client=client).generate(
        _request(options={"seed": 7, "aspect_ratio": "3:4"})
    )
    config = client.models.calls[0]["config"]
    assert config.seed == 7
    assert config.add_watermark is False


@requires_sdk
def test_the_model_is_overridable_without_a_code_change(monkeypatch):
    monkeypatch.setenv("KDP_GOOGLE_IMAGE_MODEL", "imagen-9.9-hypothetical")
    client = _ok_client()
    GoogleImagenProvider(client=client).generate(_request())
    assert client.models.calls[0]["model"] == "imagen-9.9-hypothetical"


# --- response handling ------------------------------------------------------


@requires_sdk
def test_a_successful_generation_carries_the_bytes_back():
    data = _png(120, 160, b"fern")
    result = GoogleImagenProvider(client=_ok_client(data)).generate(_request())
    assert result.ok is True
    assert result.data == data
    assert result.provider == "google-imagen"
    assert result.file_format == "png"


@requires_sdk
def test_the_actual_returned_size_is_recorded_not_the_requested_one():
    """The model picks the output size; asset QA compares record against file."""
    result = GoogleImagenProvider(client=_ok_client(_png(120, 160, b"x"))).generate(
        _request(width=2520, height=3360)
    )
    assert (result.width, result.height) == (120, 160)


@requires_sdk
def test_a_safety_filtered_image_is_a_failure_with_the_reason():
    client = StubClient(StubResponse([StubGenerated(image=None, rai_filtered_reason="blocked: policy")]))
    result = GoogleImagenProvider(client=client).generate(_request())
    assert result.ok is False
    assert "blocked: policy" in result.reason


@requires_sdk
def test_an_empty_response_is_a_failure_not_a_crash():
    result = GoogleImagenProvider(client=StubClient(StubResponse([]))).generate(_request())
    assert result.ok is False
    assert "no images" in result.reason


@requires_sdk
def test_a_transport_error_becomes_a_recorded_failure():
    """A page that could not be made must stay visibly missing."""
    client = StubClient(error=RuntimeError("connection reset"))
    result = GoogleImagenProvider(client=client).generate(_request())
    assert result.ok is False
    assert "RuntimeError" in result.reason and "connection reset" in result.reason


def test_no_credential_reaches_the_result(monkeypatch):
    monkeypatch.setenv("KDP_GOOGLE_API_KEY", "sk-SECRET-CANARY")
    result = GoogleImagenProvider(client=_ok_client()).generate(_request())
    serialised = repr(result.meta) + result.provider + result.model + (result.reason or "")
    assert "SECRET-CANARY" not in serialised


@requires_sdk
def test_meta_carries_provenance_not_endpoints():
    result = GoogleImagenProvider(client=_ok_client()).generate(_request())
    assert set(result.meta) == {"mime_type", "enhanced_prompt", "rai_filtered_reason"}


# --- through the pipeline ---------------------------------------------------


@requires_sdk
def test_the_runner_drives_it_like_any_other_provider(tmp_path):
    """The point of the provider interface: nothing above it changes."""
    from kdp.generation import generate_book
    from fixtures import build_manifest

    manifest = build_manifest()
    provider = GoogleImagenProvider(client=_ok_client(_png(64, 80, b"x")))
    outcome = generate_book(
        manifest, provider, tmp_path / "assets", now="2026-01-01T00:00:00+00:00"
    )

    assert outcome.ok
    record = outcome.manifest.attempts(manifest.spec.art_pages[0].page_id)[-1]
    assert record.provider == "google-imagen"
    assert record.status.value == "pending"
    assert record.ai_role.value == "generated"
    assert record.prompt_version == "v1"


# --- what the pinned SDK actually offers ------------------------------------
#
# These read the installed google-genai package rather than the stub. They make
# no network call: every assertion is against a type description or against a
# guard that fires before any request is built. They are canaries — if a version
# bump changes the vocabulary in kdp/generation.py, or lifts the restriction
# below, one of them fails and the change gets looked at instead of discovered
# by a paid run.



def _described(model, field: str) -> str:
    return model.model_fields[field].description or ""


@requires_sdk
def test_the_sizes_we_name_are_the_ones_the_sdk_documents():
    from google.genai import types

    from kdp.generation import NAMED_OUTPUT_SIZES

    documented = _described(types.ImageConfig, "image_size")
    for label, _pixels in NAMED_OUTPUT_SIZES:
        assert f"`{label}`" in documented


@requires_sdk
def test_four_k_is_offered_by_the_content_path_and_not_the_imagen_one():
    """The two configs do not have the same vocabulary, and it matters.

    ``GenerateImagesConfig`` is Imagen's ``:predict`` shape and documents 1K and
    2K. ``ImageConfig``, which rides inside ``GenerateContentConfig`` for the
    Gemini image models, documents 4K as well. A full-page 8.5x11 interior needs
    more than 2K to clear 300 DPI, so which config a provider sends decides
    whether the page can reach print resolution at all.
    """
    from google.genai import types

    assert "4K" not in _described(types.GenerateImagesConfig, "image_size")
    assert "`4K`" in _described(types.ImageConfig, "image_size")


@requires_sdk
def test_the_ratios_we_send_are_legal_in_both_request_shapes():
    from google.genai import types

    from kdp.generation import SUPPORTED_ASPECT_RATIOS

    imagen = _described(types.GenerateImagesConfig, "aspect_ratio")
    content = _described(types.ImageConfig, "aspect_ratio")
    for label, _ratio in SUPPORTED_ASPECT_RATIOS:
        assert f'"{label}"' in imagen
        assert f'"{label}"' in content


@requires_sdk
def test_generate_images_is_refused_outright_on_an_api_key():
    """The provider's current call path does not work on the pinned SDK.

    ``google-genai`` 2.20.0 deprecated ``generate_images`` and disabled it for
    the Gemini Developer API — an API key reaches this guard, not the network.
    Recorded here rather than in a conversation so that changing the provider's
    path, or unpinning the SDK, has to confront it.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key="not-a-real-key", vertexai=False)
    assert client._api_client.vertexai is False

    with pytest.warns(Warning):
        with pytest.raises(ValueError) as caught:
            client.models.generate_images(
                model="imagen-4.0-generate-001",
                prompt="line art of a fern",
                config=types.GenerateImagesConfig(number_of_images=1),
            )
    assert "not in Gemini Developer API mode" in str(caught.value)
