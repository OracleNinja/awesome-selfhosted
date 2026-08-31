"""The Gemini image provider, exercised without spending anything.

Every test drives the ``generate_content``-shaped stub from ``conftest`` —
``candidates[].content.parts[].inline_data``, the same attribute names the
installed ``google.genai`` types carry. Nothing reaches the network and no key
is needed, so CI never pays for a picture.

The tests at the bottom read the installed SDK instead of the stub. They are
canaries: a stub that has drifted from the real types would let every test above
pass while production failed on the first paid call.
"""

from __future__ import annotations

import pytest

from kdp.generation import build_request
from kdp.providers import METERED, get_provider
from kdp.providers.base import GenerationRequest, ProviderUnavailable
from kdp.providers.google_gemini import (
    CREDENTIAL_ENV_VAR,
    DEFAULT_IMAGE_SIZE,
    DEFAULT_MODEL,
    MODEL_ENV_VAR,
    NEGATIVE_HEADING,
    NO_PEOPLE_PROHIBITION,
    RESPONSE_MODALITIES,
    GoogleGeminiImageProvider,
    compose_prompt,
    sdk_available,
)
from kdp.providers.mock import _png
from conftest import (
    StubBlob,
    StubCandidate,
    StubContent,
    StubContentClient,
    StubContentResponse,
    StubPart,
    StubPromptFeedback,
    gemini_client_returning,
    image_response,
)

#: The provider builds genuine ``GenerateContentConfig`` and ``ImageConfig``
#: objects, so a stub client is not enough to drive it — the SDK has to be
#: installed. The stdlib-only CI job has none, and a test that quietly degraded
#: to a failed call there would assert nothing while still reporting green.
requires_sdk = pytest.mark.skipif(
    not sdk_available(), reason="the generation extra is optional"
)

#: Every credential the SDK would otherwise pick up on its own, plus the cloud
#: ones that happen to be lying around in a build environment.
AMBIENT_CREDENTIALS = (
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_GENAI_USE_ENTERPRISE",
    "CLOUDSDK_AUTH_ACCESS_TOKEN",
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
        options={"aspect_ratio": "3:4", "image_size": "4K"},
    )
    fields.update(overrides)
    return GenerationRequest(**fields)


@pytest.fixture(scope="module")
def four_k_png() -> bytes:
    """A page at the size 4K is expected to produce for a 3:4 request."""
    return _png(3072, 4096, b"a fern")


# --- availability and spend ------------------------------------------------


def test_it_is_metered_so_generation_demands_confirm_spend():
    assert "google-gemini" in METERED


def test_it_is_registered_under_its_own_name():
    assert isinstance(get_provider("google-gemini"), GoogleGeminiImageProvider)


def test_unavailable_without_a_key(monkeypatch):
    monkeypatch.delenv(CREDENTIAL_ENV_VAR, raising=False)
    provider = get_provider("google-gemini")

    assert provider.readiness()["credential_present"] is False
    assert provider.available() is False
    if sdk_available():
        # Without the extra installed, the missing SDK is reported first, and
        # saying so is more useful than naming a variable that would not help.
        assert CREDENTIAL_ENV_VAR in provider.unavailable_reason()


def test_generating_without_a_key_raises_rather_than_calling(monkeypatch):
    monkeypatch.delenv(CREDENTIAL_ENV_VAR, raising=False)
    expected = "No API key" if sdk_available() else "SDK is not installed"
    with pytest.raises(ProviderUnavailable, match=expected):
        get_provider("google-gemini").generate(_request())


def test_a_key_in_the_environment_makes_it_available(monkeypatch):
    monkeypatch.setenv(CREDENTIAL_ENV_VAR, "test-key-not-real")
    assert get_provider("google-gemini").available() is sdk_available()


def test_the_model_is_overridable_without_a_code_change(monkeypatch):
    monkeypatch.setenv(MODEL_ENV_VAR, "gemini-experimental-image")
    assert GoogleGeminiImageProvider().model == "gemini-experimental-image"
    monkeypatch.delenv(MODEL_ENV_VAR)
    assert GoogleGeminiImageProvider().model == DEFAULT_MODEL


# --- no ambient credential ever becomes a bill ------------------------------


def test_an_ambient_credential_is_not_mistaken_for_a_budget(monkeypatch):
    """The SDK reads GOOGLE_API_KEY and GEMINI_API_KEY when given no key.

    A build environment often has one, along with a cloud token put there for
    something else entirely. None of them is authorisation to buy pictures for
    a book, so the provider names one variable and reads only that.
    """
    monkeypatch.delenv(CREDENTIAL_ENV_VAR, raising=False)
    for name in AMBIENT_CREDENTIALS:
        monkeypatch.setenv(name, "ambient-value-not-a-book-budget")

    provider = get_provider("google-gemini")
    assert provider.configured() is False
    assert provider.available() is False
    with pytest.raises(ProviderUnavailable):
        provider.generate(_request())


@requires_sdk
def test_the_client_is_built_with_our_key_and_never_vertex(monkeypatch):
    monkeypatch.setenv(CREDENTIAL_ENV_VAR, "kdp-key-not-real")
    monkeypatch.setenv("GOOGLE_API_KEY", "ambient-key-not-ours")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")

    client = GoogleGeminiImageProvider()._build_client()

    assert client._api_client.vertexai is False
    assert client._api_client.api_key == "kdp-key-not-real"


@requires_sdk
def test_a_client_that_came_up_in_enterprise_mode_is_refused(monkeypatch):
    """Being wrong about this does not raise, it bills someone."""
    from google import genai

    monkeypatch.setenv(CREDENTIAL_ENV_VAR, "kdp-key-not-real")

    class Wrong:
        _api_client = type("Api", (), {"vertexai": True, "api_key": "kdp-key-not-real"})()

    monkeypatch.setattr(genai, "Client", lambda **kwargs: Wrong())
    with pytest.raises(ProviderUnavailable, match="Enterprise"):
        GoogleGeminiImageProvider()._build_client()


@requires_sdk
def test_a_client_using_some_other_key_is_refused(monkeypatch):
    from google import genai

    monkeypatch.setenv(CREDENTIAL_ENV_VAR, "kdp-key-not-real")

    class Wrong:
        _api_client = type("Api", (), {"vertexai": False, "api_key": "somebody-elses"})()

    monkeypatch.setattr(genai, "Client", lambda **kwargs: Wrong())
    with pytest.raises(ProviderUnavailable, match="not using the key"):
        GoogleGeminiImageProvider()._build_client()


# --- request construction --------------------------------------------------


@requires_sdk
def test_the_request_asks_for_an_image_at_four_k():
    client = gemini_client_returning(_png(64, 80, b"x"))
    GoogleGeminiImageProvider(client=client).generate(_request())

    call = client.models.calls[0]
    assert call["model"] == DEFAULT_MODEL
    assert call["config"].response_modalities == list(RESPONSE_MODALITIES)
    assert call["config"].image_config.image_size == "4K"
    assert call["config"].image_config.aspect_ratio == "3:4"


@requires_sdk
def test_four_k_is_asked_for_even_when_the_plan_names_no_size():
    """Omitting the size is what returned 768x768 and a 73 DPI page."""
    client = gemini_client_returning(_png(64, 80, b"x"))
    GoogleGeminiImageProvider(client=client).generate(
        _request(options={"aspect_ratio": "3:4"})
    )
    config = client.models.calls[0]["config"]
    assert config.image_config.image_size == DEFAULT_IMAGE_SIZE == "4K"


@requires_sdk
def test_person_generation_is_not_sent_because_the_sdk_refuses_it():
    """It is not an omission — ``_ImageConfig_to_mldev`` raises on it.

    Sending it would fail the whole request rather than being ignored, so the
    rule moves into the prohibitions where it can at least be stated.
    """
    client = gemini_client_returning(_png(64, 80, b"x"))
    GoogleGeminiImageProvider(client=client).generate(_request())

    config = client.models.calls[0]["config"]
    assert config.image_config.person_generation is None
    assert NO_PEOPLE_PROHIBITION in client.models.calls[0]["contents"]


@requires_sdk
def test_the_batch_request_and_the_smoke_request_are_built_the_same_way(book_prompt):
    """build_request is shared, and this provider must not reinterpret it."""
    client = gemini_client_returning(_png(64, 80, b"x"))
    request = build_request(book_prompt, attempt=1)
    GoogleGeminiImageProvider(client=client).generate(request)

    config = client.models.calls[0]["config"]
    assert config.image_config.image_size == request.options["image_size"]
    assert config.image_config.aspect_ratio == request.options["aspect_ratio"]


@pytest.fixture
def book_prompt():
    from fixtures import build_manifest

    return build_manifest().prompt_plan.prompts[0]


# --- the two halves of the prompt stay apart --------------------------------


def test_the_prohibitions_are_stated_separately_not_mixed_into_the_subject():
    text = compose_prompt(_request())

    assert text.startswith("Subject: a fern")
    assert NEGATIVE_HEADING in text
    # The prohibitions come after the subject, not woven through it.
    assert text.index("Subject: a fern") < text.index(NEGATIVE_HEADING)
    assert "colour, shading, text" in text


def test_the_no_people_rule_is_added_once_and_only_when_missing():
    once = compose_prompt(_request())
    assert once.count(NO_PEOPLE_PROHIBITION) == 1

    already = compose_prompt(
        _request(prompt_negative=f"colour, {NO_PEOPLE_PROHIBITION}")
    )
    assert already.count(NO_PEOPLE_PROHIBITION) == 1


def test_a_prompt_with_no_prohibitions_still_refuses_people():
    assert NO_PEOPLE_PROHIBITION in compose_prompt(_request(prompt_negative=""))


# --- a successful 4K response ----------------------------------------------


@requires_sdk
def test_a_four_k_response_comes_back_with_its_real_dimensions(four_k_png):
    result = GoogleGeminiImageProvider(
        client=gemini_client_returning(four_k_png)
    ).generate(_request())

    assert result.ok
    assert result.data == four_k_png
    assert (result.width, result.height) == (3072, 4096)
    assert result.file_format == "png"
    assert result.provider == "google-gemini"


@requires_sdk
def test_a_four_k_page_clears_three_hundred_dpi_over_the_live_area(four_k_png):
    """The whole reason this provider exists, in one assertion."""
    from kdp.specs import MIN_IMAGE_DPI

    result = GoogleGeminiImageProvider(
        client=gemini_client_returning(four_k_png)
    ).generate(_request())

    # The book: 8.5x11, no bleed, a 7.875 x 10.5 in live area.
    assert result.width / 7.875 >= MIN_IMAGE_DPI
    assert result.height / 10.5 >= MIN_IMAGE_DPI


@requires_sdk
def test_the_actual_returned_size_is_recorded_not_the_requested_one():
    """A page requested at 4K that comes back smaller must say so.

    Recording the request would hide exactly the failure this provider was
    written to stop: the file on disk is what G9 measures.
    """
    result = GoogleGeminiImageProvider(
        client=gemini_client_returning(_png(768, 768, b"x"))
    ).generate(_request())

    assert (result.width, result.height) == (768, 768)


@requires_sdk
def test_prose_returned_alongside_the_image_is_walked_past():
    client = StubContentClient(
        image_response(_png(64, 80, b"x"), text="Here is your line art!")
    )
    result = GoogleGeminiImageProvider(client=client).generate(_request())

    assert result.ok
    assert result.data == _png(64, 80, b"x")


# --- MIME handling ----------------------------------------------------------


@requires_sdk
def test_the_returned_mime_type_decides_the_file_extension():
    client = StubContentClient(image_response(_png(64, 80, b"x"), "image/jpeg"))
    result = GoogleGeminiImageProvider(client=client).generate(_request())

    assert result.ok
    assert result.file_format == "jpeg"
    assert result.meta["mime_type"] == "image/jpeg"


@requires_sdk
def test_a_part_that_is_not_an_image_is_refused():
    """Fail closed rather than write unknown bytes into the asset tree."""
    client = StubContentClient(image_response(b"not an image", "application/json"))
    result = GoogleGeminiImageProvider(client=client).generate(_request())

    assert result.ok is False
    assert "not an image" in result.reason


# --- safety filtering and missing bytes -------------------------------------


@requires_sdk
def test_a_blocked_prompt_is_a_failure_with_the_reason():
    client = StubContentClient(
        StubContentResponse(
            candidates=[],
            prompt_feedback=StubPromptFeedback(block_reason="IMAGE_SAFETY"),
        )
    )
    result = GoogleGeminiImageProvider(client=client).generate(_request())

    assert result.ok is False
    assert "blocked before generation" in result.reason
    assert "IMAGE_SAFETY" in result.reason


@requires_sdk
@pytest.mark.parametrize(
    "finish", ["IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT", "IMAGE_RECITATION", "NO_IMAGE"]
)
def test_a_refused_candidate_is_a_failure_naming_the_finish_reason(finish):
    client = StubContentClient(
        StubContentResponse(
            candidates=[
                StubCandidate(
                    content=StubContent(parts=[]),
                    finish_reason=finish,
                    finish_message="the model declined",
                )
            ]
        )
    )
    result = GoogleGeminiImageProvider(client=client).generate(_request())

    assert result.ok is False
    assert finish in result.reason
    assert "the model declined" in result.reason


@requires_sdk
def test_no_candidates_at_all_is_a_failure_not_a_crash():
    result = GoogleGeminiImageProvider(
        client=StubContentClient(StubContentResponse(candidates=None))
    ).generate(_request())

    assert result.ok is False
    assert "no candidates" in result.reason


@requires_sdk
def test_a_response_with_no_image_part_is_a_failure():
    client = StubContentClient(
        StubContentResponse(
            candidates=[
                StubCandidate(
                    content=StubContent(parts=[StubPart(text="I cannot draw that.")]),
                    finish_reason="STOP",
                )
            ]
        )
    )
    result = GoogleGeminiImageProvider(client=client).generate(_request())

    assert result.ok is False
    assert "no image bytes" in result.reason


@requires_sdk
def test_an_image_part_with_no_bytes_is_a_failure():
    client = StubContentClient(
        StubContentResponse(
            candidates=[
                StubCandidate(
                    content=StubContent(
                        parts=[StubPart(inline_data=StubBlob(data=None))]
                    )
                )
            ]
        )
    )
    result = GoogleGeminiImageProvider(client=client).generate(_request())

    assert result.ok is False
    assert "empty" in result.reason


@requires_sdk
def test_a_transport_error_becomes_a_recorded_failure_not_an_exception():
    client = StubContentClient(error=RuntimeError("connection reset"))
    result = GoogleGeminiImageProvider(client=client).generate(_request())

    assert result.ok is False
    assert "connection reset" in result.reason


# --- provenance -------------------------------------------------------------


@requires_sdk
def test_meta_carries_provenance_and_never_a_credential(monkeypatch):
    monkeypatch.setenv(CREDENTIAL_ENV_VAR, "sk-CANARY-NOT-REAL")
    result = GoogleGeminiImageProvider(
        client=gemini_client_returning(_png(64, 80, b"x"))
    ).generate(_request())

    assert result.meta["model_version"] == "stub-model-version"
    assert result.meta["response_id"] == "stub-response-id"
    assert result.meta["total_token_count"] == 33
    assert "CANARY" not in repr(result)


@requires_sdk
def test_the_runner_records_an_attempt_through_this_provider(tmp_path):
    """The attempt ledger is unchanged: one page in, one provenance record out."""
    from fixtures import build_manifest
    from kdp.generation import generate_book

    manifest = build_manifest()
    provider = GoogleGeminiImageProvider(
        client=gemini_client_returning(_png(64, 80, b"x"))
    )

    outcome = generate_book(
        manifest, provider, tmp_path, now="2026-01-01T00:00:00+00:00"
    )

    assert outcome.ok, outcome.failed
    pages = [p.page_id for p in manifest.spec.art_pages]
    for page in pages:
        records = [r for r in outcome.manifest.assets if r.page == page]
        assert len(records) == 1, page
        assert records[0].provider == "google-gemini"
        assert records[0].tool == DEFAULT_MODEL
        assert records[0].attempt == 1


# --- the stub matches the SDK it stands in for ------------------------------


@requires_sdk
def test_the_stub_uses_the_attribute_names_the_sdk_uses():
    from google.genai import types

    assert "inline_data" in types.Part.model_fields
    assert set(StubBlob.__dataclass_fields__) <= set(types.Blob.model_fields)
    assert set(StubCandidate.__dataclass_fields__) <= set(types.Candidate.model_fields)
    assert set(StubContentResponse.__dataclass_fields__) <= set(
        types.GenerateContentResponse.model_fields
    )
    assert "block_reason" in types.GenerateContentResponsePromptFeedback.model_fields


@requires_sdk
def test_every_refusal_reason_we_handle_is_one_the_sdk_defines():
    from google.genai import types

    from kdp.providers.google_gemini import REFUSAL_FINISH_REASONS

    defined = {member.value for member in types.FinishReason}
    assert REFUSAL_FINISH_REASONS <= defined


@requires_sdk
def test_the_sdk_refuses_person_generation_on_the_developer_api():
    """Documents why requirement 6 cannot be met as a parameter."""
    from google.genai import _api_client, types
    from google import genai

    class Stop(Exception):
        pass

    def blocked(self, *args, **kwargs):
        raise Stop()

    original = _api_client.BaseApiClient.request
    _api_client.BaseApiClient.request = blocked
    try:
        client = genai.Client(api_key="not-a-real-key", vertexai=False)
        with pytest.raises(ValueError, match="not in Gemini Developer API mode"):
            client.models.generate_content(
                model="gemini-3-pro-image-preview",
                contents="line art of a fern",
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(person_generation="ALLOW_NONE"),
                ),
            )
    finally:
        _api_client.BaseApiClient.request = original


@requires_sdk
def test_the_serialised_request_is_the_one_the_api_will_receive():
    """The closest thing to proving a paid call before making one.

    Everything above asserts on typed objects. This one lets the SDK's own
    converters turn them into the request body and checks *that*, because a
    config object that looks right and serialises wrong is exactly the failure
    that costs money. The transport is replaced first, so nothing leaves.
    """
    import json

    from google import genai
    from google.genai import _api_client

    from kdp.providers.google_gemini import compose_prompt

    captured: dict = {}

    class Stop(Exception):
        pass

    def no_network(self, method, path, request_dict, http_options=None):
        captured.update(path=path, body=request_dict)
        raise Stop()

    original = _api_client.BaseApiClient.request
    _api_client.BaseApiClient.request = no_network
    try:
        client = genai.Client(api_key="not-a-real-key", vertexai=False)
        provider = GoogleGeminiImageProvider()
        request = _request()
        try:
            client.models.generate_content(
                model=provider.model,
                contents=compose_prompt(request),
                config=provider._config(request),
            )
        except Stop:
            pass
    finally:
        _api_client.BaseApiClient.request = original

    assert captured["path"] == f"models/{DEFAULT_MODEL}:generateContent"

    body = json.loads(json.dumps(captured["body"], default=str))
    generation = body["generationConfig"]
    assert generation["responseModalities"] == ["IMAGE"]
    assert generation["imageConfig"] == {"aspectRatio": "3:4", "imageSize": "4K"}
    assert "personGeneration" not in generation["imageConfig"]

    text = body["contents"][0]["parts"][0]["text"]
    assert text.startswith("Subject: a fern")
    assert NEGATIVE_HEADING in text
    assert NO_PEOPLE_PROHIBITION in text
